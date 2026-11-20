# Daily refresh for Windows Task Scheduler. Start PostGIS and append the CLI output
# to data/processed/gunluk_log.txt. Run from any directory.

$ErrorActionPreference = "Continue"
$proj = Split-Path -Parent $PSScriptRoot
Set-Location $proj

# Fall back to the default uv installation directory.
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }

$log = Join-Path $proj "data\processed\gunluk_log.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"`n=== $ts - Starting daily refresh ===" | Out-File -FilePath $log -Append -Encoding utf8

# Docker Desktop may still be starting at login. Wait for the engine, then for PostGIS.
if (-not (Test-Path -LiteralPath $uv) -or -not (Get-Command docker -ErrorAction SilentlyContinue)) {
  "Error: uv and Docker are required." | Out-File $log -Append -Encoding utf8
  exit 1
}
function Test-DockerReady { docker info *> $null; return ($LASTEXITCODE -eq 0) }
if (-not (Test-DockerReady)) {
  $dd = @("C:\Program Files\Docker\Docker\Docker Desktop.exe",
          "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($dd) {
    Start-Process $dd -WindowStyle Hidden
    "Waiting for Docker Desktop to start..." | Out-File $log -Append -Encoding utf8
  }
  for ($i = 0; ($i -lt 72) -and -not (Test-DockerReady); $i++) { Start-Sleep -Seconds 5 }
}
if (Test-DockerReady) {
  & docker compose up -d *>> $log
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  for ($i = 0; $i -lt 36; $i++) {
    $st = docker inspect -f "{{.State.Health.Status}}" yatirimradar-db 2>$null
    if ($st -eq "healthy") { break }
    Start-Sleep -Seconds 5
  }
  "Database health: $st" | Out-File $log -Append -Encoding utf8
  if ($st -ne "healthy") { exit 1 }
} else {
  "Error: Docker did not start. Check Docker Desktop." | Out-File $log -Append -Encoding utf8
  exit 1
}

$env:PYTHONIOENCODING = "utf-8"
& $uv run yatirimradar daily *>> $log
$code = $LASTEXITCODE
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Finished (exit $code) ===" | Out-File -FilePath $log -Append -Encoding utf8
exit $code
