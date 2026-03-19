<#
.SYNOPSIS
Creates the first Django superuser for the Docker Compose deployment.

.DESCRIPTION
Starts the supported Compose stack if needed, checks whether users already
exist, and then runs `python manage.py createsuperuser` inside the web
container. This is intended as a friendly fallback when the app has no users
yet and the operator needs a direct way to create the first admin account.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$EnvFilePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "-> $Message" -ForegroundColor Cyan
}

function Fail-Helper {
    param([Parameter(Mandatory)][string]$Message)
    throw [System.InvalidOperationException]::new($Message)
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

$RepoRootPath = (Resolve-Path -LiteralPath $RepoRoot).Path

if ([string]::IsNullOrWhiteSpace($EnvFilePath)) {
    $EnvFilePath = Join-Path $RepoRootPath ".env"
}

if (-not (Test-Path -LiteralPath $EnvFilePath)) {
    Fail-Helper "No .env file was found. Run `Start Tresorapide.cmd` first so the Docker deployment is configured."
}

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) {
    $fallbackDockerPath = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $fallbackDockerPath) {
        $dockerCommand = Get-Item -LiteralPath $fallbackDockerPath
    }
}

if (-not $dockerCommand) {
    Fail-Helper "Docker was not found. Install Docker Desktop, start it, then rerun this helper."
}

function Invoke-Docker {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$CaptureOutput
    )

    $command = @(
        $dockerCommand.Source,
        "compose",
        "--env-file",
        $EnvFilePath
    ) + $Arguments

    Write-Host "  $($command -join ' ')" -ForegroundColor DarkGray

    $previousLocation = Get-Location
    try {
        Set-Location $RepoRootPath
        if ($CaptureOutput) {
            $output = & $dockerCommand.Source "compose" "--env-file" $EnvFilePath @Arguments 2>&1
            $exitCode = $LASTEXITCODE
            if ($exitCode -ne 0) {
                throw "Command failed with exit code $exitCode.`n$([string]::Join([Environment]::NewLine, @($output)))"
            }
            return [string]::Join([Environment]::NewLine, @($output))
        }

        & $dockerCommand.Source "compose" "--env-file" $EnvFilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Set-Location $previousLocation
    }
}

Write-Section "Create first Tresorapide superuser"
Write-Host "This helper uses the running Docker Compose stack."

Write-Section "Starting the app stack"
Invoke-Docker -Arguments @("up", "-d", "db", "web")

Write-Section "Checking current users"
$userCountScript = "from django.contrib.auth import get_user_model; print(get_user_model().objects.count())"
$userCountOutput = Invoke-Docker -Arguments @("exec", "-T", "web", "python", "manage.py", "shell", "-c", $userCountScript) -CaptureOutput
$userCountLine = ($userCountOutput -split "(`r`n|`n|`r)" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1)

$userCount = 0
if (-not [int]::TryParse($userCountLine.Trim(), [ref]$userCount)) {
    Fail-Helper "Could not determine the existing user count from: $userCountLine"
}

if ($userCount -gt 0) {
    Write-Host "Tresorapide already has $userCount user(s)." -ForegroundColor Yellow
    Write-Host "If you still need another admin account, run:"
    Write-Host "docker compose --env-file `"$EnvFilePath`" exec web python manage.py createsuperuser"
    exit 0
}

Write-Section "Interactive Django prompt"
Write-Step "Starting Django's createsuperuser prompt..."
Invoke-Docker -Arguments @("exec", "web", "python", "manage.py", "createsuperuser")

Write-Host ""
Write-Host "Superuser creation finished." -ForegroundColor Green
Write-Host "You can now sign in at http://localhost:8000/ and assign app roles from Django admin."
