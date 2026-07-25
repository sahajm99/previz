# Install the deploy watcher as a Windows Scheduled Task.
#
#   .\scripts\install-watcher.ps1              install and start
#   .\scripts\install-watcher.ps1 -Uninstall   remove
#
# WHY THIS EXISTS. Cloud Build runs our pipeline correctly when invoked, but
# trigger creation is refused on this project: `gcloud builds triggers create`
# returns INVALID_ARGUMENT even for a minimal webhook trigger with no repo
# attached, so it is not our arguments. GitHub Actions is out too, because
# Workload Identity needs iam.workloadIdentityPools.create and a key is refused by
# constraints/iam.disableServiceAccountKeyCreation. Nothing in GCP or GitHub will
# call us, so something local has to poll.
#
# A shell loop in a terminal is not good enough: it dies with the session, which
# already happened once here and left production stale while everything looked
# fine. As a scheduled task it runs at boot, with no user logged in, and restarts
# itself if it stops.
#
# Replace this the moment triggers become available. It is a workaround for a
# permission problem, not a design.
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$TaskName = "MagicHourDeployWatcher"
$Root = Split-Path -Parent $PSScriptRoot

if ($Uninstall) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "· removed $TaskName"
  } else {
    Write-Host "· $TaskName was not installed"
  }
  return
}

$bash = "C:\Program Files\Git\bin\bash.exe"
if (-not (Test-Path $bash)) {
  $found = (Get-Command bash.exe -ErrorAction SilentlyContinue).Source
  if (-not $found) { throw "Git Bash not found. Install Git for Windows." }
  $bash = $found
}

# The OAuth client id has to be baked in, because a scheduled task inherits none
# of your shell environment. Without it the deploy would drop the variable and
# silently turn sign-in off, which is the exact bug --update-env-vars was added
# to prevent.
$clientId = "775345250143-5ga300fac55k4lrguii014nfr5g4sqj2.apps.googleusercontent.com"
$inner = "export GOOGLE_OAUTH_CLIENT_ID='$clientId'; cd '$($Root -replace '\\','/')' && ./scripts/watch-deploy.sh"

$action = New-ScheduledTaskAction -Execute $bash -Argument "-lc `"$inner`""

# At boot AND at logon. At boot alone would not start until a reboot; at logon
# alone would not survive one.
$triggers = @(
  (New-ScheduledTaskTrigger -AtStartup),
  (New-ScheduledTaskTrigger -AtLogOn)
)

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -RestartCount 999 `
  -ExecutionTimeLimit (New-TimeSpan -Days 0)   # 0 means never kill it

# Registering a task with an -AtStartup trigger requires elevation. Checked up
# front and failed loudly, because the first version of this script printed
# "installed and started" after Register-ScheduledTask had already thrown Access
# Denied, which is the same false success this repo's smoke gate exists to prevent.
$admin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $admin) {
  Write-Host ""
  Write-Host "  NOT INSTALLED. This needs an elevated shell."
  Write-Host "  An -AtStartup trigger is a machine level task, so Windows requires it."
  Write-Host ""
  Write-Host "  Right click PowerShell, Run as Administrator, then:"
  Write-Host "      cd '$Root'"
  Write-Host "      .\scripts\install-watcher.ps1"
  Write-Host ""
  exit 1
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
  -Settings $settings -Description "Polls the Magic Hour team repo and deploys main to Cloud Run" `
  -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

# Verify rather than assume. Register can succeed and the task still not be running.
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { throw "registration reported success but the task does not exist" }

Write-Host ""
Write-Host "  installed and started: $TaskName  (state: $($task.State))"
Write-Host "  it now survives closing this terminal and rebooting."
Write-Host ""
Write-Host "  status   Get-ScheduledTask $TaskName"
Write-Host "  stop     Stop-ScheduledTask $TaskName"
Write-Host "  remove   .\scripts\install-watcher.ps1 -Uninstall"
Write-Host ""
