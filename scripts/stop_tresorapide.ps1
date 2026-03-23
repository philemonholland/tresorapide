<#
.SYNOPSIS
Stops the supported Tresorapide Docker Compose deployment on Windows.

.DESCRIPTION
Runs `docker compose down` from the repository root. By default it keeps
the named data volumes intact. Use `-RemoveVolumes` only when you
explicitly want to delete persisted application data.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$RemoveVolumes,
    [string]$RepoRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRootPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
else {
    $RepoRootPath = (Resolve-Path $RepoRoot).Path
}

function Write-Section {
    param([Parameter(Mandatory)][string]$Message)

    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Invoke-ComposeCommand {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$CaptureOutput,
        [switch]$IgnoreErrors
    )

    $previousLocation = Get-Location
    try {
        Set-Location $RepoRootPath
        if ($CaptureOutput) {
            $output = & docker @Arguments 2>&1
            $exitCode = $LASTEXITCODE
            $text = [string]::Join([Environment]::NewLine, @($output))
            if ($exitCode -ne 0 -and -not $IgnoreErrors) {
                throw "Command failed ($exitCode): docker $($Arguments -join ' ')`n$text"
            }

            return [pscustomobject]@{
                ExitCode = $exitCode
                Output   = $text
            }
        }

        & docker @Arguments
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0 -and -not $IgnoreErrors) {
            throw "Command failed ($exitCode): docker $($Arguments -join ' ')"
        }

        return [pscustomobject]@{
            ExitCode = $exitCode
            Output   = ""
        }
    }
    finally {
        Set-Location $previousLocation
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Start Docker Desktop or install Docker, then try again."
}

$runningServicesResult = Invoke-ComposeCommand -Arguments @("compose", "ps", "--status", "running", "--services") -CaptureOutput -IgnoreErrors
$runningServices = @(
    $runningServicesResult.Output -split "`r?`n" |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)

if ($runningServices.Count -eq 0) {
    Write-Section "Tresorapide is already stopped"
    exit 0
}

Write-Section "Stopping Tresorapide"
Write-Host "Running services: $($runningServices -join ', ')"

$composeArgs = @("compose", "down", "--remove-orphans")
if ($RemoveVolumes) {
    $composeArgs += "-v"
}

if (-not $PSCmdlet.ShouldProcess($RepoRootPath, "docker $($composeArgs -join ' ')")) {
    Write-Host "Stop skipped." -ForegroundColor Yellow
    exit 0
}

Invoke-ComposeCommand -Arguments $composeArgs | Out-Null

if ($RemoveVolumes) {
    Write-Host "Tresorapide stopped and volumes were removed." -ForegroundColor Green
}
else {
    Write-Host "Tresorapide stopped. Data volumes were preserved." -ForegroundColor Green
}
