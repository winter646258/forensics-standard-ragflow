<#
.SYNOPSIS
    打出可分发的发行包。

.DESCRIPTION
    只打包 Git 已跟踪的文件，因此天然排除标准正文、切片明细与部署凭据
    （它们都在 .gitignore 里）。产出 dist/<仓库名>-<版本>.zip，
    压缩包内含一个与版本同名的顶层目录。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\package-release.ps1 -Version v0.1.0
#>
[CmdletBinding()]
param(
    [string]$Version = 'v0.1.0',
    [string]$RepositoryName = 'forensics-standard-ragflow',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'dist' }
$packageName = "$RepositoryName-$Version"
$stagingRoot = Join-Path ([IO.Path]::GetTempPath()) ("pkg-" + [Guid]::NewGuid().ToString('N'))
$staging = Join-Path $stagingRoot $packageName
$zipPath = Join-Path $OutputDirectory "$packageName.zip"

# 只取已跟踪文件：标准正文与凭据都在 .gitignore 中，不会进入发行包。
Push-Location $root
try {
    # git 把文件名按 UTF-8 输出，而 PowerShell 5.1 默认用控制台代码页解码，
    # 中文文件名会变成乱码，后续 Copy-Item 便找不到文件。显式声明 UTF-8。
    $consoleEncoding = [Console]::OutputEncoding
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
    try {
        $tracked = & git -c core.quotepath=false ls-files
        if ($LASTEXITCODE -ne 0) { throw 'git ls-files 失败，请在仓库根目录执行。' }
    } finally {
        [Console]::OutputEncoding = $consoleEncoding
    }
} finally {
    Pop-Location
}
$tracked = @($tracked | Where-Object { $_ -and $_.Trim() })
if (-not $tracked) { throw '没有可打包的文件。' }

# 出于安全考虑，明确拒绝任何疑似正文或凭据的路径。
$forbidden = @(
    'ragflow-import/01-正文Markdown/',
    'ragflow-import/01-正文Markdown-清洗版/',
    'ragflow-import/01-正文Markdown-重转/',
    '03-正文切片关联.jsonl',
    '.env.local'
)
$blocked = $tracked | Where-Object { $p = $_; $forbidden | Where-Object { $p -like "*$_*" } }
if ($blocked) {
    throw "发行包中出现了禁止打包的文件：`n$($blocked -join "`n")"
}

New-Item -ItemType Directory -Path $staging -Force | Out-Null
foreach ($file in $tracked) {
    $source = Join-Path $root $file
    $target = Join-Path $staging $file
    $targetDir = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $targetDir)) { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }
    Copy-Item -LiteralPath $source -Destination $target -Force
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

# 用 .NET 打包并显式声明 UTF-8 文件名，让非中文环境解压时也不出现乱码。
# 注意：以包内目录为源，顶层才会是 <仓库名>-<版本>，而不是临时目录名。
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory(
    $staging,
    $zipPath,
    [IO.Compression.CompressionLevel]::Optimal,
    $true,
    [Text.Encoding]::UTF8
)

Remove-Item -LiteralPath $stagingRoot -Recurse -Force

$zip = Get-Item -LiteralPath $zipPath
$totalBytes = ($tracked | ForEach-Object { (Get-Item -LiteralPath (Join-Path $root $_)).Length } | Measure-Object -Sum).Sum
Write-Output "发行包：$($zip.FullName)"
Write-Output ("文件数：{0}，原始大小：{1:N1} MB，压缩后：{2:N1} MB" -f $tracked.Count, ($totalBytes / 1MB), ($zip.Length / 1MB))
Write-Output "未包含：标准正文、切片关联明细、部署凭据（均由 .gitignore 与检查规则排除）。"
