from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Vertex AI. No api key: ADC locally, the attached service account on Cloud
    # Run. gemini_api_key is kept only so any leftover AI Studio path still
    # imports; nothing new should use it.
    gcp_project: str = "nyu-ai-builder26nyc-9338"
    gcp_location: str = "us-central1"
    gemini_api_key: str = ""

    google_maps_api_key: str = ""

    # Google Sign-In. An OAuth 2.0 Web client id from
    # console.cloud.google.com/apis/credentials. Empty means auth is DISABLED and
    # everyone runs as one local user, so the app still boots and demos without a
    # console round trip. See app/auth.py.
    google_oauth_client_id: str = ""

    max_shots: int = 6
    max_tool_calls: int = 15
    # Hard ceiling per story so a retry loop cannot eat the budget. The lab
    # project expires today and image generation is the only thing here that
    # costs real money per call.
    max_images_per_story: int = 60

    locations_cache_dir: str = "demo_cache/locations"
    locations_db_file: str = "demo_cache/locations_db.json"
    canvas_db_file: str = "demo_cache/canvas_board.json"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
