[CmdletBinding()]
param(
    [ValidateSet('up', 'down', 'ps', 'logs', 'config', 'pull')]
    [string]$Action = 'ps',
    [string]$ComposeRoot = 'D:\RAGFlow\ragflow\docker',
    [string]$Service = 'ragflow-cpu',
    [string]$DockerPath = '',
    [string]$EnvFile = ''
)

$ErrorActionPreference = 'Stop'

if (-not $DockerPath) {
    $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($dockerCommand) {
        $DockerPath = $dockerCommand.Source
    } else {
        $knownPath = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
        if (Test-Path -LiteralPath $knownPath) {
            $DockerPath = $knownPath
        }
    }
}

if (-not $DockerPath -or -not (Test-Path -LiteralPath $DockerPath)) {
    throw 'Docker CLI not found. Start or repair Docker Desktop before running this script.'
}

$composeFile = Join-Path $ComposeRoot 'docker-compose.yml'
$overrideFile = Join-Path $PSScriptRoot 'docker-compose.override.yml'
foreach ($requiredPath in @($ComposeRoot, $composeFile, $overrideFile)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "缺少部署文件或目录：$requiredPath"
    }
}

# The deployment uses per-instance random passwords from .env.local. Recreating
# containers without it would feed different passwords to every service while
# the existing data volumes keep the original ones, breaking the stack.
if (-not $EnvFile) {
    $localEnv = Join-Path $PSScriptRoot '.env.local'
    if (Test-Path -LiteralPath $localEnv) { $EnvFile = $localEnv }
}
if (-not $EnvFile -or -not (Test-Path -LiteralPath $EnvFile)) {
    throw '找不到 .env.local，无法安全重建容器。请先恢复该部署专用环境文件。'
}

$composeArgs = @(
    'compose',
    '--project-directory', $ComposeRoot,
    '--env-file', $EnvFile,
    '-f', $composeFile,
    '-f', $overrideFile
)

switch ($Action) {
    'up' {
        & $DockerPath @composeArgs 'up' '-d'
    }
    'down' {
        # Do not remove volumes; the knowledge base state lives there.
        & $DockerPath @composeArgs 'down'
    }
    'ps' {
        & $DockerPath @composeArgs 'ps'
    }
    'logs' {
        & $DockerPath @composeArgs 'logs' '--tail=200' $Service
    }
    'config' {
        & $DockerPath @composeArgs 'config'
    }
    'pull' {
        & $DockerPath @composeArgs 'pull'
    }
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
