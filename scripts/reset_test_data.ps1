<#
.SYNOPSIS
Resets Tresorapide development transaction data.

.DESCRIPTION
Starts the supported Docker Compose stack if needed, then runs the
`reset_test_data` Django management command inside the web container.

This preserves:
- houses, members, apartments, residencies
- budget years and sub-budgets
- user accounts

It deletes:
- expenses
- bons de commande (active + archived/void)
- OCR records and uploaded receipt files
- audit entries
- merchant rows
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$DryRun,
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
    Fail-Helper "No .env file was found. Run Start Tresorapide.cmd first so the Docker deployment is configured."
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

    if ($DryRun) {
        Write-Host "  [dry-run] $($command -join ' ')" -ForegroundColor DarkGray
        if ($CaptureOutput) {
            return ""
        }
        return
    }

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

Write-Section "Reset Tresorapide test data"
Write-Host "This helper removes transactional development data while preserving budgets and members."

Write-Section "Starting the app stack"
Invoke-Docker -Arguments @("up", "-d", "db", "web")

Write-Section "Running Django reset command"
$resetArguments = @("exec", "web", "python", "manage.py", "reset_test_data")
if ($Yes) {
    $resetArguments += "--yes"
}
Invoke-Docker -Arguments $resetArguments

Write-Host ""
if ($DryRun) {
    Write-Host "Dry run completed - no data was deleted." -ForegroundColor Yellow
}
else {
    Write-Host "Reset command finished." -ForegroundColor Green
}
