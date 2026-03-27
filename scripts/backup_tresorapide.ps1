<#
.SYNOPSIS
    Interactive backup wrapper for Tresorapide.
    Backs up the PostgreSQL database, media files, and secrets.
.DESCRIPTION
    Prompts for a backup destination on first run and remembers the choice.
    Calls compose_backup.py with --include-secrets for a complete backup.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir

# ---------------------------------------------------------------------------
# Persistent backup location
# ---------------------------------------------------------------------------
$AppName       = 'Tresorapide'
$LocalAppData  = $env:LOCALAPPDATA
if (-not $LocalAppData) { $LocalAppData = Join-Path $env:USERPROFILE 'AppData\Local' }
$AppDataDir    = Join-Path $LocalAppData $AppName
$SettingsFile  = Join-Path $AppDataDir 'backup_settings.json'

function Read-BackupSettings {
    if (Test-Path $SettingsFile) {
        try {
            return Get-Content $SettingsFile -Raw | ConvertFrom-Json
        } catch {
            return $null
        }
    }
    return $null
}

function Save-BackupSettings([string]$BackupRoot) {
    if (-not (Test-Path $AppDataDir)) {
        New-Item -ItemType Directory -Path $AppDataDir -Force | Out-Null
    }
    @{ backup_root = $BackupRoot } | ConvertTo-Json | Set-Content $SettingsFile -Encoding UTF8
}

function Get-BackupRoot {
    $settings = Read-BackupSettings
    $DefaultRoot = Join-Path $AppDataDir 'backups'

    if ($settings -and $settings.backup_root) {
        $saved = $settings.backup_root
        Write-Host ""
        Write-Host "Current backup location: $saved"
        Write-Host ""
        Write-Host "  [1] Use this location"
        Write-Host "  [2] Choose a different location"
        Write-Host ""
        $choice = Read-Host "Your choice (1)"
        if ($choice -eq '2') {
            return Prompt-ForLocation -Default $saved
        }
        return $saved
    }

    Write-Host ""
    Write-Host "No backup location configured yet."
    return Prompt-ForLocation -Default $DefaultRoot
}

function Prompt-ForLocation([string]$Default) {
    Write-Host ""
    Write-Host "Where should backups be saved?"
    Write-Host "  Default: $Default"
    Write-Host ""
    $input = Read-Host "Backup path (press Enter for default)"
    if ([string]::IsNullOrWhiteSpace($input)) {
        $chosen = $Default
    } else {
        $chosen = $input.Trim()
    }
    $resolved = [System.IO.Path]::GetFullPath($chosen)
    Save-BackupSettings $resolved
    Write-Host "Backup location saved to settings."
    return $resolved
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Host "=== $AppName Backup ==="

$BackupRoot = Get-BackupRoot

Write-Host ""
Write-Host "Starting backup to: $BackupRoot"
Write-Host ""

# Resolve the Python interpreter inside the venv
$Python = Join-Path $ProjectDir 'venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    Write-Host "ERROR: Python not found at $Python" -ForegroundColor Red
    Write-Host "Make sure the virtual environment is set up."
    exit 1
}

$BackupScript = Join-Path $ScriptDir 'compose_backup.py'

# Read secrets dir from .env for explicit passing
$SecretsDir = $null
$EnvFile = Join-Path $ProjectDir '.env'
if (Test-Path $EnvFile) {
    $match = Select-String -Path $EnvFile -Pattern '^\s*SECRETS_DIR\s*=\s*(.+)' | Select-Object -First 1
    if ($match) {
        $SecretsDir = $match.Matches[0].Groups[1].Value.Trim()
    }
}

$backupArgs = @(
    $BackupScript,
    '--output-root', $BackupRoot,
    '--include-secrets',
    '--allow-unprotected-storage'
)
if ($SecretsDir) {
    $backupArgs += @('--secrets-dir', $SecretsDir)
}

& $Python @backupArgs
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "Backup completed successfully." -ForegroundColor Green
} else {
    Write-Host "Backup failed (exit code $exitCode)." -ForegroundColor Red
}

exit $exitCode
