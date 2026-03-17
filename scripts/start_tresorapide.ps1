<#
.SYNOPSIS
Starts the supported Tresorapide Docker Compose deployment on Windows.

.DESCRIPTION
Guides first-run setup, writes or repairs the Docker-focused .env file,
starts Docker Desktop when possible, runs docker compose up -d --build,
waits for the readiness endpoint, optionally offers createsuperuser, and
opens the app in a browser.

.EXAMPLE
.\scripts\start_tresorapide.ps1

.EXAMPLE
.\scripts\start_tresorapide.ps1 -DryRun -NonInteractive -AcceptDefaults -NoBrowser
#>
[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$AcceptDefaults,
    [switch]$NoBrowser,
    [switch]$DryRun,
    [string]$RepoRoot,
    [string]$EnvFilePath,
    [int]$DockerStartupTimeoutSeconds = 240,
    [int]$ReadinessTimeoutSeconds = 240
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

function Write-Success {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host $Message -ForegroundColor Green
}

function Write-WarningText {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host $Message -ForegroundColor Yellow
}

function Fail-Launcher {
    param([Parameter(Mandatory)][string]$Message)
    throw [System.InvalidOperationException]::new($Message)
}

function Get-CommandArguments {
    param([Parameter(Mandatory)][string[]]$Command)
    if ($Command.Count -le 1) {
        return @()
    }

    return $Command[1..($Command.Count - 1)]
}

function Invoke-ExternalCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$Command,
        [switch]$CaptureOutput,
        [switch]$IgnoreErrors,
        [switch]$Quiet
    )

    if (-not $Quiet) {
        Write-Host "  $($Command -join ' ')" -ForegroundColor DarkGray
    }

    $previousLocation = Get-Location
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        Set-Location $script:RepoRootPath
        $ErrorActionPreference = "Continue"
        $commandPath = $Command[0]
        $commandArgs = @(Get-CommandArguments -Command $Command)

        if ($CaptureOutput) {
            $output = & $commandPath @commandArgs 2>&1
            $exitCode = $LASTEXITCODE
            $text = [string]::Join([Environment]::NewLine, @($output))

            if ($exitCode -ne 0 -and -not $IgnoreErrors) {
                $detail = if ([string]::IsNullOrWhiteSpace($text)) {
                    "The command exited with code $exitCode."
                }
                else {
                    $text.Trim()
                }

                throw [System.ComponentModel.Win32Exception]::new(
                    "Command failed ($exitCode): $($Command -join ' ')`n$detail"
                )
            }

            return [pscustomobject]@{
                ExitCode = $exitCode
                Output   = $text
            }
        }

        & $commandPath @commandArgs
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0 -and -not $IgnoreErrors) {
            throw [System.ComponentModel.Win32Exception]::new(
                "Command failed ($exitCode): $($Command -join ' ')"
            )
        }

        return [pscustomobject]@{
            ExitCode = $exitCode
            Output   = ""
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Set-Location $previousLocation
    }
}

function Parse-DotEnvFile {
    param([Parameter(Mandatory)][string]$Path)

    $values = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*$' -or $line -match '^\s*#') {
            continue
        }

        $parts = $line -split '=', 2
        if ($parts.Count -ne 2) {
            continue
        }

        $key = $parts[0].Trim()
        if ([string]::IsNullOrWhiteSpace($key)) {
            continue
        }

        $values[$key] = $parts[1]
    }

    return $values
}

function Merge-DotEnvValues {
    param(
        [Parameter(Mandatory)]$TemplateValues,
        [Parameter(Mandatory)]$ExistingValues
    )

    $merged = [ordered]@{}
    foreach ($entry in $TemplateValues.GetEnumerator()) {
        $merged[$entry.Key] = $entry.Value
    }

    foreach ($entry in $ExistingValues.GetEnumerator()) {
        $merged[$entry.Key] = $entry.Value
    }

    return $merged
}

function Render-DotEnvFile {
    param(
        [Parameter(Mandatory)][string]$TemplatePath,
        [Parameter(Mandatory)]$Values
    )

    $renderedLines = New-Object System.Collections.Generic.List[string]
    $seenKeys = @{}

    foreach ($line in Get-Content -LiteralPath $TemplatePath -Encoding UTF8) {
        if ($line -match '^\s*([^#=\s]+)\s*=(.*)$') {
            $key = $Matches[1]
            if ($Values.Contains($key)) {
                $renderedLines.Add("$key=$($Values[$key])")
            }
            else {
                $renderedLines.Add($line)
            }

            $seenKeys[$key] = $true
            continue
        }

        $renderedLines.Add($line)
    }

    $extraKeys = @()
    foreach ($key in $Values.Keys) {
        if (-not $seenKeys.ContainsKey($key)) {
            $extraKeys += $key
        }
    }

    if ($extraKeys.Count -gt 0) {
        if ($renderedLines.Count -gt 0 -and $renderedLines[$renderedLines.Count - 1] -ne "") {
            $renderedLines.Add("")
        }

        $renderedLines.Add("# Additional local overrides")
        foreach ($key in ($extraKeys | Sort-Object)) {
            $renderedLines.Add("$key=$($Values[$key])")
        }
    }

    return @($renderedLines)
}

function Write-DotEnvFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string[]]$Lines
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, $Lines, $utf8NoBom)
}

function New-RandomString {
    param(
        [int]$Length = 32,
        [string]$Alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    )

    $bytes = New-Object byte[] $Length
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }

    $characters = for ($index = 0; $index -lt $Length; $index++) {
        $Alphabet[$bytes[$index] % $Alphabet.Length]
    }

    return -join $characters
}

function Get-LocalIPv4Addresses {
    $addresses = New-Object System.Collections.Generic.List[string]

    $getNetIpCommand = Get-Command Get-NetIPAddress -ErrorAction SilentlyContinue
    if ($getNetIpCommand) {
        try {
            $candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
                Where-Object {
                    $_.IPAddress -and
                    $_.IPAddress -notlike "127.*" -and
                    $_.IPAddress -notlike "169.254.*"
                } |
                Select-Object -ExpandProperty IPAddress -Unique

            foreach ($candidate in $candidates) {
                if (-not $addresses.Contains($candidate)) {
                    $addresses.Add($candidate)
                }
            }
        }
        catch {
        }
    }

    if ($addresses.Count -eq 0) {
        try {
            $candidates = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
                Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } |
                ForEach-Object { $_.IPAddressToString } |
                Where-Object {
                    $_ -notlike "127.*" -and
                    $_ -notlike "169.254.*"
                } |
                Select-Object -Unique

            foreach ($candidate in $candidates) {
                if (-not $addresses.Contains($candidate)) {
                    $addresses.Add($candidate)
                }
            }
        }
        catch {
        }
    }

    $scored = foreach ($address in $addresses) {
        $score = 2
        if ($address -like "192.168.*") {
            $score = 0
        }
        elseif ($address -like "10.*") {
            $score = 1
        }
        elseif ($address -match '^172\.(1[6-9]|2[0-9]|3[0-1])\.') {
            $score = 1
        }

        [pscustomobject]@{
            Address = $address
            Score   = $score
        }
    }

    return @($scored | Sort-Object Score, Address | Select-Object -ExpandProperty Address)
}

function Get-DefaultLanHostSuggestion {
    $addresses = @(Get-LocalIPv4Addresses)
    if ($addresses.Count -gt 0) {
        return $addresses[0]
    }

    if (-not [string]::IsNullOrWhiteSpace($env:COMPUTERNAME)) {
        return $env:COMPUTERNAME
    }

    return "localhost"
}

function Get-UniqueStringList {
    param(
        [AllowEmptyCollection()]
        [string[]]$Items = @()
    )

    $seen = @{}
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($item in $Items) {
        $trimmed = $item.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed)) {
            continue
        }

        $key = $trimmed.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $result.Add($trimmed)
        }
    }

    return @($result)
}

function ConvertTo-HostList {
    param([Parameter(Mandatory)][string]$Value)

    $pieces = $Value -split ','
    return @(Get-UniqueStringList -Items $pieces)
}

function Test-IsValidPort {
    param([Parameter(Mandatory)][string]$Value)

    $port = 0
    if (-not [int]::TryParse($Value, [ref]$port)) {
        return $false
    }

    return $port -ge 1 -and $port -le 65535
}

function Test-IsValidHostToken {
    param([Parameter(Mandatory)][string]$HostName)

    if ($HostName.Contains("://") -or $HostName.Contains("/") -or $HostName.Contains(":")) {
        return $false
    }

    if ($HostName -match '^\d{1,3}(\.\d{1,3}){3}$') {
        foreach ($segment in ($HostName -split '\.')) {
            $octet = 0
            if (-not [int]::TryParse($segment, [ref]$octet)) {
                return $false
            }

            if ($octet -lt 0 -or $octet -gt 255) {
                return $false
            }
        }

        return $true
    }

    return $HostName -match '^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$'
}

function Test-IsValidHostList {
    param([Parameter(Mandatory)][string]$Value)

    $hosts = @(ConvertTo-HostList -Value $Value)
    if ($hosts.Count -eq 0) {
        return $false
    }

    foreach ($hostName in $hosts) {
        if (-not (Test-IsValidHostToken -HostName $hostName)) {
            return $false
        }
    }

    return $true
}

function Get-PortFromBinding {
    param(
        [string]$Binding,
        [int]$DefaultPort = 5432
    )

    if ([string]::IsNullOrWhiteSpace($Binding)) {
        return $DefaultPort
    }

    $trimmed = $Binding.Trim()
    if ($trimmed -match ':(\d+)$') {
        return [int]$Matches[1]
    }

    if ($trimmed -match '^\d+$') {
        return [int]$trimmed
    }

    return $DefaultPort
}

function Get-CustomLanHostsFromAllowedHosts {
    param([string]$AllowedHosts)

    $allHosts = @(ConvertTo-HostList -Value $AllowedHosts)
    $customHosts = @()
    foreach ($hostName in $allHosts) {
        if ($hostName -in @("localhost", "127.0.0.1", "web", "192.168.1.50")) {
            continue
        }

        $customHosts += $hostName
    }

    return @(Get-UniqueStringList -Items $customHosts)
}

function Build-AllowedHosts {
    param(
        [AllowEmptyCollection()]
        [string[]]$LanHosts = @()
    )

    return ((Get-UniqueStringList -Items (@("localhost", "127.0.0.1", "web") + $LanHosts)) -join ",")
}

function Build-TrustedOrigins {
    param(
        [AllowEmptyCollection()]
        [string[]]$LanHosts = @(),
        [Parameter(Mandatory)][string]$AppPort
    )

    $origins = @(
        "http://localhost:$AppPort",
        "http://127.0.0.1:$AppPort"
    )

    foreach ($lanHost in $LanHosts) {
        $origins += "http://$lanHost`:$AppPort"
    }

    return ((Get-UniqueStringList -Items $origins) -join ",")
}

function Test-NeedsSecretGeneration {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }

    return $Value -match 'change-me|django-insecure-local-dev-key-change-me'
}

function Test-NeedsPasswordGeneration {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }

    return $Value -match 'change-me|^tresorapide$'
}

function Test-NeedsNetworkSetup {
    param([Parameter(Mandatory)]$Values)

    $allowedHosts = [string]$Values["DJANGO_ALLOWED_HOSTS"]
    $origins = [string]$Values["DJANGO_CSRF_TRUSTED_ORIGINS"]
    $appPort = [string]$Values["APP_PUBLISHED_PORT"]
    $dbPublishedPort = [string]$Values["POSTGRES_PUBLISHED_PORT"]
    $customHosts = @(Get-CustomLanHostsFromAllowedHosts -AllowedHosts $allowedHosts)

    if ($allowedHosts -match '192\.168\.1\.50') {
        return $true
    }

    if ($origins -match '192\.168\.1\.50') {
        return $true
    }

    if ($customHosts.Count -eq 0) {
        return $true
    }

    if (-not (Test-IsValidPort -Value $appPort)) {
        return $true
    }

    if ($dbPublishedPort -notmatch '^127\.0\.0\.1:\d+$') {
        return $true
    }

    return $false
}

function Get-SetupValue {
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [Parameter(Mandatory)][string]$DefaultValue,
        [Parameter(Mandatory)][scriptblock]$Validator,
        [Parameter(Mandatory)][string]$ValidationMessage
    )

    if ($AcceptDefaults) {
        Write-Host "$Prompt`n  Using default: $DefaultValue"
        return $DefaultValue
    }

    if ($NonInteractive) {
        Fail-Launcher (
            "Setup is required, but -NonInteractive was used without -AcceptDefaults. " +
            "Run Start Tresorapide.cmd normally, or re-run the PowerShell script with -AcceptDefaults."
        )
    }

    while ($true) {
        $response = Read-Host "$Prompt [$DefaultValue]"
        if ([string]::IsNullOrWhiteSpace($response)) {
            $response = $DefaultValue
        }

        if (& $Validator $response) {
            return $response
        }

        Write-WarningText $ValidationMessage
    }
}

function Resolve-EnvironmentConfiguration {
    param(
        [Parameter(Mandatory)]$Values,
        [Parameter(Mandatory)][bool]$EnvExists
    )

    $changed = $false
    $generatedKeys = New-Object System.Collections.Generic.List[string]
    $normalizedMessages = New-Object System.Collections.Generic.List[string]

    if ([string]$Values["DATABASE_ENGINE"] -ne "django.db.backends.postgresql") {
        $Values["DATABASE_ENGINE"] = "django.db.backends.postgresql"
        $changed = $true
        $normalizedMessages.Add("Set DATABASE_ENGINE to PostgreSQL for the Docker deployment path.")
    }

    if ([string]$Values["POSTGRES_HOST"] -ne "db") {
        $Values["POSTGRES_HOST"] = "db"
        $changed = $true
        $normalizedMessages.Add("Set POSTGRES_HOST to the Compose service name 'db'.")
    }

    if ([string]$Values["POSTGRES_PORT"] -ne "5432") {
        $Values["POSTGRES_PORT"] = "5432"
        $changed = $true
        $normalizedMessages.Add("Set POSTGRES_PORT to the container port 5432.")
    }

    if (Test-NeedsSecretGeneration -Value ([string]$Values["DJANGO_SECRET_KEY"])) {
        $Values["DJANGO_SECRET_KEY"] = New-RandomString -Length 64
        $changed = $true
        $generatedKeys.Add("DJANGO_SECRET_KEY")
    }

    if (Test-NeedsPasswordGeneration -Value ([string]$Values["POSTGRES_PASSWORD"])) {
        $Values["POSTGRES_PASSWORD"] = New-RandomString -Length 32
        $changed = $true
        $generatedKeys.Add("POSTGRES_PASSWORD")
    }

    $appPort = [string]$Values["APP_PUBLISHED_PORT"]
    if (-not (Test-IsValidPort -Value $appPort)) {
        $appPort = "8000"
    }

    $dbPort = (Get-PortFromBinding -Binding ([string]$Values["POSTGRES_PUBLISHED_PORT"]) -DefaultPort 5432).ToString()
    $lanHosts = @(Get-CustomLanHostsFromAllowedHosts -AllowedHosts ([string]$Values["DJANGO_ALLOWED_HOSTS"]))

    if (-not $EnvExists -or (Test-NeedsNetworkSetup -Values $Values)) {
        Write-Section "Guided first-run setup"
        if (-not $EnvExists) {
            Write-Host "No .env file was found, so I'll create one for the Docker deployment path."
        }
        else {
            Write-Host ".env still has placeholder or incomplete network settings, so I'll repair it."
        }

        if ($generatedKeys.Count -gt 0) {
            Write-Host "Secure values were generated for: $($generatedKeys -join ', ')."
        }

        $defaultLanHost = if ($lanHosts.Count -gt 0) {
            $lanHosts -join ","
        }
        else {
            Get-DefaultLanHostSuggestion
        }

        if ($defaultLanHost -eq "localhost") {
            Write-WarningText "I could not confidently detect a LAN address. If other devices cannot reach this PC, rerun setup and enter the actual LAN IP."
        }

        $lanHostAnswer = Get-SetupValue `
            -Prompt "LAN hostname or IPv4 address for this PC (comma-separated if you want more than one)" `
            -DefaultValue $defaultLanHost `
            -Validator { param($candidate) Test-IsValidHostList -Value $candidate } `
            -ValidationMessage "Enter hostnames or IPv4 addresses only, without http:// or port numbers."

        $appPort = Get-SetupValue `
            -Prompt "Published app port on this PC" `
            -DefaultValue $appPort `
            -Validator { param($candidate) Test-IsValidPort -Value $candidate } `
            -ValidationMessage "Enter a port number between 1 and 65535."

        $dbPort = Get-SetupValue `
            -Prompt "Published PostgreSQL host port (kept local to this PC)" `
            -DefaultValue $dbPort `
            -Validator { param($candidate) Test-IsValidPort -Value $candidate } `
            -ValidationMessage "Enter a port number between 1 and 65535."

        $lanHosts = @(ConvertTo-HostList -Value $lanHostAnswer)
        $Values["DJANGO_ALLOWED_HOSTS"] = Build-AllowedHosts -LanHosts $lanHosts
        $Values["DJANGO_CSRF_TRUSTED_ORIGINS"] = Build-TrustedOrigins -LanHosts $lanHosts -AppPort $appPort
        $Values["APP_PUBLISHED_PORT"] = $appPort
        $Values["POSTGRES_PUBLISHED_PORT"] = "127.0.0.1:$dbPort"
        $changed = $true
    }
    else {
        $lanHosts = @(Get-CustomLanHostsFromAllowedHosts -AllowedHosts ([string]$Values["DJANGO_ALLOWED_HOSTS"]))
        $expectedAllowedHosts = Build-AllowedHosts -LanHosts $lanHosts
        $expectedOrigins = Build-TrustedOrigins -LanHosts $lanHosts -AppPort $appPort
        $expectedDbBinding = "127.0.0.1:$dbPort"

        if ([string]$Values["DJANGO_ALLOWED_HOSTS"] -ne $expectedAllowedHosts) {
            $Values["DJANGO_ALLOWED_HOSTS"] = $expectedAllowedHosts
            $changed = $true
            $normalizedMessages.Add("Normalized DJANGO_ALLOWED_HOSTS.")
        }

        if ([string]$Values["DJANGO_CSRF_TRUSTED_ORIGINS"] -ne $expectedOrigins) {
            $Values["DJANGO_CSRF_TRUSTED_ORIGINS"] = $expectedOrigins
            $changed = $true
            $normalizedMessages.Add("Normalized DJANGO_CSRF_TRUSTED_ORIGINS to match the app port.")
        }

        if ([string]$Values["POSTGRES_PUBLISHED_PORT"] -ne $expectedDbBinding) {
            $Values["POSTGRES_PUBLISHED_PORT"] = $expectedDbBinding
            $changed = $true
            $normalizedMessages.Add("Kept PostgreSQL published only on 127.0.0.1.")
        }

        if ([string]$Values["APP_PUBLISHED_PORT"] -ne $appPort) {
            $Values["APP_PUBLISHED_PORT"] = $appPort
            $changed = $true
            $normalizedMessages.Add("Normalized APP_PUBLISHED_PORT.")
        }
    }

    return [pscustomobject]@{
        Values             = $Values
        Changed            = $changed
        GeneratedKeys      = @($generatedKeys)
        NormalizedMessages = @($normalizedMessages)
        LanHosts           = @(Get-CustomLanHostsFromAllowedHosts -AllowedHosts ([string]$Values["DJANGO_ALLOWED_HOSTS"]))
        AppPort            = [string]$Values["APP_PUBLISHED_PORT"]
        DatabasePort       = (Get-PortFromBinding -Binding ([string]$Values["POSTGRES_PUBLISHED_PORT"]) -DefaultPort 5432).ToString()
    }
}

function Find-DockerCliPath {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerCommand -and $dockerCommand.Source) {
        return $dockerCommand.Source
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"),
        (Join-Path $env:LocalAppData "Programs\Docker\Docker\resources\bin\docker.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Find-LegacyComposePath {
    $composeCommand = Get-Command docker-compose -ErrorAction SilentlyContinue
    if ($composeCommand -and $composeCommand.Source) {
        return $composeCommand.Source
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker-compose.exe"),
        (Join-Path $env:LocalAppData "Programs\Docker\Docker\resources\bin\docker-compose.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Get-DockerDesktopPath {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LocalAppData "Programs\Docker\Docker\Docker Desktop.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Get-ComposeCommand {
    param([Parameter(Mandatory)][string]$DockerCliPath)

    $composeProbe = Invoke-ExternalCommand `
        -Command @($DockerCliPath, "compose", "version") `
        -CaptureOutput `
        -IgnoreErrors `
        -Quiet

    if ($composeProbe.ExitCode -eq 0) {
        return @($DockerCliPath, "compose")
    }

    $legacyPath = Find-LegacyComposePath
    if ($legacyPath) {
        $legacyProbe = Invoke-ExternalCommand `
            -Command @($legacyPath, "version") `
            -CaptureOutput `
            -IgnoreErrors `
            -Quiet

        if ($legacyProbe.ExitCode -eq 0) {
            return @($legacyPath)
        }
    }

    return $null
}

function Test-DockerEngineReady {
    $probe = Invoke-ExternalCommand `
        -Command @($script:DockerCliPath, "info", "--format", "{{.ServerVersion}}") `
        -CaptureOutput `
        -IgnoreErrors `
        -Quiet

    return $probe.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($probe.Output)
}

function Wait-ForDockerEngine {
    param([int]$TimeoutSeconds = 240)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastMessage = ""

    while ((Get-Date) -lt $deadline) {
        $probe = Invoke-ExternalCommand `
            -Command @($script:DockerCliPath, "info", "--format", "{{.ServerVersion}}") `
            -CaptureOutput `
            -IgnoreErrors `
            -Quiet

        if ($probe.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($probe.Output)) {
            Write-Success "Docker is ready."
            return
        }

        $lastMessage = $probe.Output.Trim()
        Start-Sleep -Seconds 5
    }

    if ([string]::IsNullOrWhiteSpace($lastMessage)) {
        Fail-Launcher "Docker did not become ready within $TimeoutSeconds seconds."
    }

    Fail-Launcher "Docker did not become ready within $TimeoutSeconds seconds. Last message: $lastMessage"
}

function Invoke-ComposeCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$CaptureOutput,
        [switch]$IgnoreErrors,
        [switch]$Quiet
    )

    $command = @($script:ComposeCommand)
    if (-not [string]::IsNullOrWhiteSpace($script:EffectiveEnvFilePath)) {
        $command += @("--env-file", $script:EffectiveEnvFilePath)
    }

    $command += $Arguments
    return Invoke-ExternalCommand `
        -Command $command `
        -CaptureOutput:$CaptureOutput `
        -IgnoreErrors:$IgnoreErrors `
        -Quiet:$Quiet
}

function Wait-ForReadiness {
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 240
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastMessage = ""

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $Url -TimeoutSec 5
            if ($response.status -eq "ok") {
                return $response
            }

            $lastMessage = "Received an unexpected readiness payload."
        }
        catch {
            $lastMessage = $_.Exception.Message
        }

        Start-Sleep -Seconds 5
    }

    Fail-Launcher "The app did not become ready at $Url within $TimeoutSeconds seconds. Last check: $lastMessage"
}

function Get-UserCount {
    $scriptText = "from django.contrib.auth import get_user_model; print(get_user_model().objects.count())"
    $result = Invoke-ComposeCommand `
        -Arguments @("exec", "-T", "web", "python", "manage.py", "shell", "-c", $scriptText) `
        -CaptureOutput

    $lines = @($result.Output -split "(`r`n|`n|`r)")
    $lastLine = $lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1
    if ([string]::IsNullOrWhiteSpace($lastLine)) {
        Fail-Launcher "The launcher could not determine how many users exist in the database."
    }

    $count = 0
    if (-not [int]::TryParse($lastLine.Trim(), [ref]$count)) {
        Fail-Launcher "The launcher could not parse the current user count from: $lastLine"
    }

    return $count
}

function Show-TroubleshootingInfo {
    param(
        [Parameter(Mandatory)][string]$AppPort,
        [Parameter(Mandatory)][string]$EnvPath
    )

    if (-not $script:ComposeCommand) {
        return
    }

    Write-Section "Troubleshooting"

    $psResult = Invoke-ComposeCommand -Arguments @("ps") -CaptureOutput -IgnoreErrors -Quiet
    if (-not [string]::IsNullOrWhiteSpace($psResult.Output)) {
        Write-Host $psResult.Output.Trim()
        Write-Host ""
    }

    $logsResult = Invoke-ComposeCommand -Arguments @("logs", "--tail", "60", "web", "db") -CaptureOutput -IgnoreErrors -Quiet
    if (-not [string]::IsNullOrWhiteSpace($logsResult.Output)) {
        Write-Host $logsResult.Output.Trim()
        Write-Host ""
    }

    Write-Host "Next steps:"
    Write-Host "  - Open Docker Desktop and confirm it says the engine is running."
    Write-Host "  - Re-run Start Tresorapide.cmd after fixing the problem."
    Write-Host "  - Review live logs with: docker compose --env-file `"$EnvPath`" logs -f web"
    Write-Host "  - If port $AppPort is busy, rerun the launcher and choose another app port."
}

function Invoke-CreatesuperuserPrompt {
    param([Parameter(Mandatory)][string]$EnvPath)

    if ($NonInteractive) {
        Write-WarningText "No users exist yet. Re-run the launcher without -NonInteractive, or run `docker compose --env-file `"$EnvPath`" exec web python manage.py createsuperuser` later."
        return
    }

    Write-Host ""
    while ($true) {
        $answer = Read-Host "No users exist yet. Create the first Django superuser now? [Y/n]"
        if ([string]::IsNullOrWhiteSpace($answer) -or $answer.Trim().ToLowerInvariant() -in @("y", "yes")) {
            Write-Step "Starting Django's createsuperuser prompt..."
            Invoke-ComposeCommand -Arguments @("exec", "web", "python", "manage.py", "createsuperuser") | Out-Null
            Write-Success "Superuser creation finished."
            Write-Host "You can adjust application roles later from Django admin if needed."
            return
        }

        if ($answer.Trim().ToLowerInvariant() -in @("n", "no")) {
            Write-WarningText "Skipping superuser creation for now. You can rerun the launcher later to create one."
            return
        }

        Write-WarningText "Please answer Y or N."
    }
}

function Show-ConfigurationSummary {
    param(
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)][string]$EnvPath,
        [Parameter(Mandatory)][bool]$EnvExistsBeforeRun
    )

    $localUrl = "http://localhost:$($Configuration.AppPort)/"
    $lanUrls = @()
    foreach ($lanHost in $Configuration.LanHosts) {
        $lanUrls += "http://$lanHost`:$($Configuration.AppPort)/"
    }

    Write-Section "Configuration"
    if ($DryRun) {
        if ($Configuration.Changed -or -not $EnvExistsBeforeRun) {
            Write-Host "Dry run only: .env would be written to $EnvPath"
        }
        else {
            Write-Host "Dry run only: existing .env would be reused."
        }
    }
    elseif ($Configuration.Changed -or -not $EnvExistsBeforeRun) {
        Write-Host ".env ready at $EnvPath"
    }
    else {
        Write-Host "Using existing .env at $EnvPath"
    }

    if ($Configuration.GeneratedKeys.Count -gt 0) {
        Write-Host "Generated secure values for: $($Configuration.GeneratedKeys -join ', ')"
    }

    foreach ($message in $Configuration.NormalizedMessages) {
        Write-Host "- $message"
    }

    Write-Host "Local app URL: $localUrl"
    foreach ($lanUrl in $lanUrls) {
        Write-Host "LAN app URL:   $lanUrl"
    }
    Write-Host "PostgreSQL stays local on 127.0.0.1:$($Configuration.DatabasePort)"
}

$temporaryEnvPath = $null
$appPortForFailure = "8000"
$stackStarted = $false

try {
    if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        $RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    }

    $script:RepoRootPath = (Resolve-Path -LiteralPath $RepoRoot).Path
    $script:ComposeFilePath = Join-Path $script:RepoRootPath "docker-compose.yml"
    $script:EnvExamplePath = Join-Path $script:RepoRootPath ".env.example"

    if (-not (Test-Path -LiteralPath $script:ComposeFilePath)) {
        Fail-Launcher "Could not find docker-compose.yml in $script:RepoRootPath"
    }

    if (-not (Test-Path -LiteralPath $script:EnvExamplePath)) {
        Fail-Launcher "Could not find .env.example in $script:RepoRootPath"
    }

    if ([string]::IsNullOrWhiteSpace($EnvFilePath)) {
        $EnvFilePath = Join-Path $script:RepoRootPath ".env"
    }

    $EnvFilePath = [System.IO.Path]::GetFullPath($EnvFilePath)
    $envExistsBeforeRun = Test-Path -LiteralPath $EnvFilePath

    Write-Section "Tresorapide launcher"
    Write-Host "This launcher manages the supported Docker Compose deployment for a trusted local network."
    if ($DryRun) {
        Write-WarningText "Dry run mode is on: no files will be changed and no containers will be started."
    }

    Write-Section "Checking Docker tools"
    $script:DockerCliPath = Find-DockerCliPath
    if (-not $script:DockerCliPath) {
        $desktopPath = Get-DockerDesktopPath
        if ($desktopPath) {
            Fail-Launcher (
                "Docker Desktop appears to be installed, but docker.exe was not found in PATH or the usual install folders. " +
                "Open Docker Desktop once, then retry the launcher."
            )
        }

        Fail-Launcher (
            "Docker Desktop is required for the supported local-network deployment path. " +
            "Install Docker Desktop, then run Start Tresorapide.cmd again."
        )
    }

    $script:ComposeCommand = Get-ComposeCommand -DockerCliPath $script:DockerCliPath
    if (-not $script:ComposeCommand) {
        Fail-Launcher (
            "Docker Compose is not available. Please update or reinstall Docker Desktop so that `docker compose` works."
        )
    }

    Write-Success "Docker CLI found."
    Write-Success "Docker Compose found."

    $dockerDesktopPath = Get-DockerDesktopPath
    $dockerReadyAtStart = Test-DockerEngineReady
    if ($dockerReadyAtStart) {
        Write-Success "Docker is already running."
    }
    elseif ($dockerDesktopPath) {
        if ($DryRun) {
            Write-Host "Dry run only: would start Docker Desktop from $dockerDesktopPath"
        }
        else {
            Write-Step "Docker is not ready yet. Starting Docker Desktop..."
            Start-Process -FilePath $dockerDesktopPath | Out-Null
            Write-Host "Docker Desktop is starting in the background."
        }
    }
    else {
        Write-WarningText "Docker is not ready yet, and the launcher could not find Docker Desktop automatically."
        if (-not $DryRun) {
            Write-Host "The launcher will still wait for Docker before starting the stack."
        }
    }

    $templateValues = Parse-DotEnvFile -Path $script:EnvExamplePath
    $existingValues = Parse-DotEnvFile -Path $EnvFilePath
    $mergedValues = Merge-DotEnvValues -TemplateValues $templateValues -ExistingValues $existingValues
    $configuration = Resolve-EnvironmentConfiguration -Values $mergedValues -EnvExists:$envExistsBeforeRun
    $appPortForFailure = $configuration.AppPort

    $renderedEnvLines = Render-DotEnvFile -TemplatePath $script:EnvExamplePath -Values $configuration.Values

    if ($configuration.Changed -or -not $envExistsBeforeRun) {
        $temporaryEnvPath = Join-Path ([System.IO.Path]::GetTempPath()) ("tresorapide-launcher-" + [Guid]::NewGuid().ToString("N") + ".env")
        Write-DotEnvFile -Path $temporaryEnvPath -Lines $renderedEnvLines
        $script:EffectiveEnvFilePath = $temporaryEnvPath
    }
    else {
        $script:EffectiveEnvFilePath = $EnvFilePath
    }

    Show-ConfigurationSummary -Configuration $configuration -EnvPath $EnvFilePath -EnvExistsBeforeRun:$envExistsBeforeRun

    Write-Section "Validating Docker Compose configuration"
    Invoke-ComposeCommand -Arguments @("config", "-q") -Quiet | Out-Null
    Write-Success "Docker Compose configuration looks valid."

    if ($DryRun) {
        Write-Section "Dry run complete"
        Write-Host "The launcher script parsed successfully, prepared the Docker .env values, and validated docker-compose.yml."
        exit 0
    }

    if ($configuration.Changed -or -not $envExistsBeforeRun) {
        Write-Step "Writing $EnvFilePath"
        Write-DotEnvFile -Path $EnvFilePath -Lines $renderedEnvLines
        Write-Success ".env updated."
        $script:EffectiveEnvFilePath = $EnvFilePath
    }

    if (-not (Test-DockerEngineReady)) {
        Write-Section "Waiting for Docker"
        Write-Host "Waiting for Docker to become ready..."
        Wait-ForDockerEngine -TimeoutSeconds $DockerStartupTimeoutSeconds
    }

    Write-Section "Starting containers"
    Invoke-ComposeCommand -Arguments @("up", "-d", "--build") | Out-Null
    $stackStarted = $true
    Write-Success "Compose stack started."

    $readinessUrl = "http://localhost:$($configuration.AppPort)/api/ready/"
    Write-Section "Waiting for the app"
    Write-Host "Checking $readinessUrl"
    $readiness = Wait-ForReadiness -Url $readinessUrl -TimeoutSeconds $ReadinessTimeoutSeconds
    Write-Success "Tresorapide is ready. Database status: $($readiness.database)"

    $userCount = Get-UserCount
    if ($userCount -eq 0) {
        Invoke-CreatesuperuserPrompt -EnvPath $EnvFilePath
    }
    else {
        Write-Host "Detected $userCount existing user(s), so no initial superuser prompt is needed."
    }

    Write-Section "Ready"
    $localUrl = "http://localhost:$($configuration.AppPort)/"
    Write-Host "Local app: $localUrl"
    foreach ($lanHost in $configuration.LanHosts) {
        Write-Host "LAN app:   http://$lanHost`:$($configuration.AppPort)/"
    }

    if ($NoBrowser) {
        Write-Host "No browser was opened because -NoBrowser was specified."
    }
    else {
        Write-Step "Opening the app in your default browser..."
        Start-Process $localUrl | Out-Null
    }

    Write-Success "All set. Future double-clicks can reuse this launcher to start or update the stack."
    exit 0
}
catch {
    Write-Host ""
    Write-Host "Tresorapide could not start." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red

    if (-not $DryRun -and $stackStarted -and $script:ComposeCommand) {
        Show-TroubleshootingInfo -AppPort $appPortForFailure -EnvPath $EnvFilePath
    }

    exit 1
}
finally {
    if ($temporaryEnvPath -and (Test-Path -LiteralPath $temporaryEnvPath)) {
        Remove-Item -LiteralPath $temporaryEnvPath -Force -ErrorAction SilentlyContinue
    }
}
