[CmdletBinding()]
param(
    [string]$DockerPath = '',
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
# Docker Desktop can be installed per-user or machine-wide; probe both, in the
# same order the CLI probe below uses, so this script works on either layout.
$desktopCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'),
    'C:\Program Files\Docker\Docker\Docker Desktop.exe'
)
$dockerDesktopExe = $desktopCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$runDir = Join-Path $env:LOCALAPPDATA 'Docker\run'
$secretsDir = Join-Path $env:LOCALAPPDATA 'docker-secrets-engine'

if (-not $DockerPath) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
        'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { $DockerPath = $candidate; break }
    }
    if (-not $DockerPath) {
        $cmd = Get-Command docker.exe -ErrorAction SilentlyContinue
        if ($cmd) { $DockerPath = $cmd.Source }
    }
}
if (-not $DockerPath -or -not (Test-Path -LiteralPath $DockerPath)) {
    throw 'Docker CLI not found. Install or repair Docker Desktop first.'
}
if (-not $dockerDesktopExe) {
    throw "Docker Desktop not found. Looked in: $($desktopCandidates -join '; ')"
}

function Test-Engine {
    # `docker version` can answer while the daemon still fails on real API
    # calls, so probe the container list as well.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & $DockerPath ps --quiet 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

if (Test-Engine) {
    Write-Output 'Docker engine already healthy.'
    & $DockerPath ps --format 'table {{.Names}}\t{{.Status}}'
    exit 0
}

Write-Output 'Engine not responding; performing a clean restart.'

# Docker Desktop leaves dangling unix-socket files behind when the backend is
# killed. They cannot be renamed by the backend on the next start, so it crashes.
Get-Process -Name 'Docker Desktop','com.docker.backend','com.docker.build','com.docker.sailor' -ErrorAction SilentlyContinue |
    ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch { } }
Start-Sleep -Seconds 5

$wslShutdown = Start-Process -FilePath 'wsl.exe' -ArgumentList '--shutdown' -NoNewWindow -PassThru
$wslShutdown | Wait-Process -Timeout 60 -ErrorAction SilentlyContinue
if (-not $wslShutdown.HasExited) { $wslShutdown | Stop-Process -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 5

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
foreach ($dir in @($runDir, $secretsDir)) {
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    $items = Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue
    if (-not $items) { continue }
    $renamed = "$dir.broken-$stamp"
    try {
        Rename-Item -LiteralPath $dir -NewName (Split-Path -Leaf $renamed) -ErrorAction Stop
        Write-Output "Archived stale sockets: $renamed"
    } catch {
        Write-Output "Could not archive ${dir}: $($_.Exception.Message)"
    }
}

Start-Process -FilePath $dockerDesktopExe
Write-Output 'Waiting for the Docker engine...'

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    Start-Sleep -Seconds 10
    if (Test-Engine) {
        Write-Output 'Docker engine is up.'
        & $DockerPath ps --format 'table {{.Names}}\t{{.Status}}'
        exit 0
    }
} while ((Get-Date) -lt $deadline)

Write-Error 'Docker engine did not become healthy in time. Check the Docker Desktop UI and its logs.'
exit 1
