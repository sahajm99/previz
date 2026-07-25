# Magic Hour infrastructure.
#
#   cd infra && terraform init && terraform apply
#
# WHAT THIS MANAGES: the APIs, the Artifact Registry repository, and the SHAPE of
# the Cloud Run service (cpu, memory, scaling, ingress, env vars).
#
# WHAT THIS DELIBERATELY DOES NOT MANAGE: the container image, and IAM.
#
# The image is owned by CI. Terraform ignores it via lifecycle.ignore_changes
# below, because otherwise every deploy would drift the state and the next
# `terraform apply` would helpfully roll production back to whatever tag was
# current when the state was written. Terraform owns the shape of the service, CI
# owns what runs inside it. Those are different jobs on different cadences and
# giving both to one tool is how a deploy pipeline starts fighting itself.
#
# IAM is absent because it cannot be applied on this project. Every binding needs
# resourcemanager.projects.setIamPolicy, which is not granted here, so the service
# accounts, role bindings and Workload Identity pool from the design spec §15 are
# commented at the bottom rather than declared. They are correct and they will
# apply unchanged on a project where the caller has owner.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  # Local state on purpose. A GCS backend is the right answer and needs a bucket
  # that outlives the lab project, which expires. Moving to a backend later is a
  # `terraform init -migrate-state` and nothing else.
}

variable "project" {
  description = "GCP project id. Note the numeric suffix: the short form fails with a misleading permission error."
  type        = string
  default     = "nyu-ai-builder26nyc-9338"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "service" {
  type    = string
  default = "magic-hour"
}

variable "google_oauth_client_id" {
  description = "OAuth 2.0 Web client id. Empty disables sign-in and the app runs as one local user."
  type        = string
  default     = ""
}

variable "google_maps_api_key" {
  description = "Enables live Places search. Empty degrades Scout to the seeded locations."
  type        = string
  default     = ""
  sensitive   = true
}

provider "google" {
  project = var.project
  region  = var.region
}

# Only what is actually used. cloudbuild is absent because builds happen in CI or
# on a laptop, not in Cloud Build, for the IAM reason in scripts/deploy.sh.
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "places.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "cloud-run-source-deploy"
  format        = "DOCKER"
  description   = "Magic Hour container images, tagged by commit sha"

  # Keep the last 20 tagged images so a rollback target always exists, and let
  # untagged layers expire. Without this the repo grows forever at ~400MB a push.
  cleanup_policies {
    id     = "keep-recent-tagged"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }
  cleanup_policies {
    id     = "drop-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s" # 7 days
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "app" {
  name     = var.service
  location = var.region

  # Public. The design has a public web service and an internal agents service;
  # that split is cancelled for today, so this one process is the whole app.
  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    # min 1 because cold starting a container that imports the Vertex SDK is four
    # to eight seconds, and on a demo stage that reads as broken.
    #
    # max 1 because the store is in process memory. A second instance would serve
    # a different story from the first, and a user's requests would land on
    # whichever one the load balancer picked. This is the single line to change
    # when Postgres replaces app/store.py.
    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }

    timeout = "600s"

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project}/cloud-run-source-deploy/${var.service}:latest"

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        # Image generation runs about 30 seconds a frame and holds a request open
        # the whole time. Without CPU always allocated, Cloud Run throttles the
        # instance between requests and those calls crawl.
        cpu_idle = false
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "GCP_PROJECT"
        value = var.project
      }
      env {
        name  = "GCP_LOCATION"
        value = var.region
      }
      env {
        name  = "GOOGLE_OAUTH_CLIENT_ID"
        value = var.google_oauth_client_id
      }
      env {
        name  = "GOOGLE_MAPS_API_KEY"
        value = var.google_maps_api_key
      }

      startup_probe {
        # /api/health and not /healthz. Cloud Run's frontend intercepts /healthz
        # and returns its own 404 without the request reaching the container.
        #
        # This path lives INSIDE the api package, so if the API failed to mount
        # the probe fails. main.py catches a bad router import by design, which
        # otherwise produces a container that starts, serves the whole UI, and
        # 404s every /api route while looking perfectly healthy.
        http_get {
          path = "/api/health"
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
        timeout_seconds       = 4
      }
    }
  }

  lifecycle {
    # CI owns the image. See the header.
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [google_project_service.apis]
}

# Anyone can reach the app; the app itself requires Google sign-in when
# GOOGLE_OAUTH_CLIENT_ID is set. Authentication is the application's job here
# rather than the platform's, so that the sign-in screen can render at all.
#
# This is the one IAM resource that might apply, because it sets policy on the
# service and not on the project. If it fails with a permission error, the
# equivalent is `gcloud run deploy --allow-unauthenticated`, which is what
# scripts/deploy.sh passes.
resource "google_cloud_run_v2_service_iam_member" "public" {
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value = google_cloud_run_v2_service.app.uri
}

output "health" {
  value = "${google_cloud_run_v2_service.app.uri}/api/health"
}

# ─────────────────────────────────────────────────────────────────────────────
# BLOCKED ON THIS PROJECT. Correct, and needs resourcemanager.projects.setIamPolicy.
# Uncomment on a project where the caller has owner, then delete the service
# account key from GitHub secrets and switch .github/workflows/deploy.yml to
# workload_identity_provider.
#
# resource "google_iam_workload_identity_pool" "github" {
#   workload_identity_pool_id = "github"
# }
#
# resource "google_iam_workload_identity_pool_provider" "github" {
#   workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
#   workload_identity_pool_provider_id = "github"
#   attribute_mapping = {
#     "google.subject"       = "assertion.sub"
#     "attribute.repository" = "assertion.repository"
#   }
#   # Not optional. Without this condition ANY repository on GitHub can exchange
#   # a token against this pool.
#   attribute_condition = "assertion.repository == 'SampreethAvvari/previz'"
#   oidc {
#     issuer_uri = "https://token.actions.githubusercontent.com"
#   }
# }
#
# resource "google_service_account" "ci" {
#   account_id   = "mh-ci"
#   display_name = "Magic Hour CI, deploy only"
# }
#
# resource "google_service_account_iam_member" "ci_wif" {
#   service_account_id = google_service_account.ci.name
#   role               = "roles/iam.workloadIdentityUser"
#   member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/SampreethAvvari/previz"
# }
#
# # Least privilege, from the design spec §13 item 4.
# resource "google_project_iam_member" "ci_roles" {
#   for_each = toset([
#     "roles/run.developer",
#     "roles/artifactregistry.writer",
#     "roles/iam.serviceAccountUser",
#   ])
#   project = var.project
#   role    = each.value
#   member  = "serviceAccount:${google_service_account.ci.email}"
# }
# ─────────────────────────────────────────────────────────────────────────────
