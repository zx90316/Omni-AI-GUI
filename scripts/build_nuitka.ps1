#Requires -Version 5.1
<#
.SYNOPSIS
  Build the complete Windows release with Nuitka standalone mode.

.DESCRIPTION
  The release intentionally uses a directory-based standalone executable. It
  does not use onefile extraction, UPX, or another executable packer. The ZIP
  contains only the compiled Manager runtime and release notices. Backend and
  Frontend remain in the source repository and are never copied into the ZIP.

.PARAMETER ReleaseVersion
  Semantic version. It must match omni_version.__version__.

.PARAMETER CertThumbprint
  Optional SHA-1 thumbprint of a code-signing certificate in CurrentUser\My.

.PARAMETER SkipInstall
  Do not install/update the Manager build dependencies.

.PARAMETER SkipZip
  Keep the release folder but do not create the distributable ZIP/checksum.

.PARAMETER OutputDirectory
  Optional output directory under the repository. The default is release/.

.PARAMETER PythonExecutable
  Optional Python executable used for dependency installation and Nuitka. This
  is useful for reproducing CI with an isolated environment.
#>
param(
    [string]$ReleaseVersion = "",
    [string]$CertThumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$OutputDirectory = "",
    [string]$PythonExecutable = "",
    [switch]$SkipInstall,
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $childFull = [IO.Path]::GetFullPath($Child)
    if (-not $childFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside '$Parent': $Child"
    }
}

function Find-SdkTool {
    param([Parameter(Mandatory = $true)][string]$Name)
    $patterns = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\$Name",
        "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\$Name"
    )
    foreach ($pattern in $patterns) {
        $hit = Get-Item $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if ($PythonExecutable) {
    $PythonCandidate = if ([IO.Path]::IsPathRooted($PythonExecutable)) {
        $PythonExecutable
    } else {
        Join-Path $Root $PythonExecutable
    }
    if (-not (Test-Path -LiteralPath $PythonCandidate -PathType Leaf)) {
        throw "Python executable was not found: $PythonCandidate"
    }
    $Python = (Resolve-Path -LiteralPath $PythonCandidate).Path
    Write-Host "Using explicit Python: $Python" -ForegroundColor DarkYellow
} elseif (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    $Python = $VenvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Create .venv or add Python 3.10-3.13 to PATH."
    }
    $Python = $pythonCommand.Source
    Write-Host "Using system Python: $Python" -ForegroundColor DarkYellow
}

$SourceVersion = (& $Python -c "from omni_version import __version__; print(__version__)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $SourceVersion) {
    throw "Unable to read omni_version.__version__."
}
if (-not $ReleaseVersion) { $ReleaseVersion = $SourceVersion }
if ($ReleaseVersion -ne $SourceVersion) {
    throw "Release version '$ReleaseVersion' does not match source version '$SourceVersion'."
}
if ($ReleaseVersion -notmatch '^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "Release version must be semantic version text: $ReleaseVersion"
}
$WindowsVersion = "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"

if (-not $SkipInstall) {
    Write-Host "==> Installing Manager build dependencies..." -ForegroundColor Cyan
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m pip install -r manager\requirements.txt -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$ProductName = "Omni-AI-Manager"
$ReleaseRoot = if ($OutputDirectory) {
    if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        [IO.Path]::GetFullPath($OutputDirectory)
    } else {
        [IO.Path]::GetFullPath((Join-Path $Root $OutputDirectory))
    }
} else {
    Join-Path $Root "release"
}
$BuildRoot = Join-Path $ReleaseRoot "_nuitka"
$FinalDir = Join-Path $ReleaseRoot $ProductName
Assert-ChildPath -Parent $Root -Child $ReleaseRoot
if (Test-Path -LiteralPath $ReleaseRoot) {
    $InstalledProject = Join-Path $FinalDir "Omni-AI-GUI"
    $InstalledProjectMarkers = @(
        (Join-Path $InstalledProject "manager\app.py"),
        (Join-Path $InstalledProject "backend\app.py"),
        (Join-Path $InstalledProject "frontend\package.json")
    )
    if (($InstalledProjectMarkers | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }).Count -eq 3) {
        throw "Refusing to clean '$ReleaseRoot' because it contains an installed Omni-AI-GUI project. Use -OutputDirectory for an isolated build."
    }
    Write-Host "==> Removing the previous release directory..." -ForegroundColor Cyan
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null

$NuitkaArgs = @(
    "-m", "nuitka",
    "--mode=standalone",
    "--enable-plugin=tk-inter",
    "--windows-console-mode=disable",
    "--assume-yes-for-downloads",
    "--follow-imports",
    "--output-filename=$ProductName.exe",
    "--output-dir=$BuildRoot",
    "--product-name=Omni AI Manager",
    "--company-name=Omni-AI-GUI Contributors",
    "--file-description=Local multimodal AI workspace manager",
    "--file-version=$WindowsVersion",
    "--product-version=$WindowsVersion",
    "--copyright=Copyright (c) 2026 Omni-AI-GUI Contributors",
    "--trademarks=Omni AI Manager",
    "--remove-output",
    "--nofollow-import-to=pytest",
    "--nofollow-import-to=unittest",
    "--nofollow-import-to=tests",
    "--nofollow-import-to=backend",
    "--nofollow-import-to=manager.model_downloader",
    "--no-deployment-flag=excluded-module-usage",
    "launch.py"
)

Write-Host "==> Building Nuitka standalone Manager v$ReleaseVersion..." -ForegroundColor Cyan
Write-Host "Nuitka args: $($NuitkaArgs -join ' ')" -ForegroundColor DarkGray
& $Python @NuitkaArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$NuitkaDist = Get-ChildItem -LiteralPath $BuildRoot -Directory |
    Where-Object { $_.Name -like "*.dist" } |
    Select-Object -First 1
if (-not $NuitkaDist) { throw "Nuitka standalone output directory was not found." }
Move-Item -LiteralPath $NuitkaDist.FullName -Destination $FinalDir

$ManifestPath = Join-Path $Root "packaging\release-manifest.json"
$ReleaseManifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($file in $ReleaseManifest.files) {
    $source = Join-Path $Root $file
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required release file is missing: $file"
    }
    $target = Join-Path $FinalDir $file
    $targetParent = Split-Path $target -Parent
    if (-not (Test-Path -LiteralPath $targetParent)) {
        New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $target -Force
}
Copy-Item -LiteralPath (Join-Path $Root "packaging\THIRD_PARTY_NOTICES.txt") `
    -Destination (Join-Path $FinalDir "THIRD_PARTY_NOTICES.txt") -Force

$ReleaseReadme = @(
    "Omni AI Manager v$ReleaseVersion",
    "================================",
    "",
    "Run",
    "  1. Extract the complete ZIP; do not copy only Omni-AI-Manager.exe.",
    "  2. Start Omni-AI-Manager.exe.",
    "  3. Select a project location when prompted; Manager clones the Backend/Frontend source there.",
    "",
    "Corporate deployment",
    "  - This is a Nuitka standalone build; it does not self-extract like onefile.",
    "  - The release does not use UPX or another executable packer.",
    "  - Sign production builds with the organization's Authenticode certificate.",
    "  - Verify the adjacent SHA-256 file or GitHub artifact attestation.",
    "  - Reputation and behavioral rules can still block new software; ask IT to approve the publisher or hash.",
    "",
    "Notes",
    "  Only Manager is compiled and distributed. Backend and Frontend are not included in this ZIP.",
    "  The AI runtime, models, and FFmpeg are explicitly installed through Manager.",
    "  The release never contains source project data, .env, databases, uploads, results, or model caches."
) -join [Environment]::NewLine
Set-Content -LiteralPath (Join-Path $FinalDir "README_RELEASE.txt") -Value $ReleaseReadme -Encoding UTF8

$ExePath = Join-Path $FinalDir "$ProductName.exe"
$ManifestTemplate = Join-Path $Root "packaging\app.manifest"
$mt = Find-SdkTool -Name "mt.exe"
if ($mt) {
    $resolvedManifest = Join-Path $ReleaseRoot "app.resolved.manifest"
    $manifestContent = Get-Content -LiteralPath $ManifestTemplate -Raw -Encoding UTF8
    $manifestContent = $manifestContent.Replace('version="0.0.0.0"', "version=`"$WindowsVersion`"")
    Set-Content -LiteralPath $resolvedManifest -Value $manifestContent -Encoding UTF8
    Write-Host "==> Embedding the asInvoker application manifest..." -ForegroundColor Cyan
    & $mt -nologo -manifest $resolvedManifest "-outputresource:$ExePath;1"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Remove-Item -LiteralPath $resolvedManifest -Force
} else {
    Write-Host "Windows SDK mt.exe not found; Nuitka's default manifest remains in use." -ForegroundColor DarkYellow
}

if ($CertThumbprint) {
    $signtool = Find-SdkTool -Name "signtool.exe"
    if (-not $signtool) { throw "signtool.exe was not found. Install the Windows SDK." }
    Write-Host "==> Applying Authenticode signature..." -ForegroundColor Cyan
    & $signtool sign /sha1 $CertThumbprint /s My /fd SHA256 /td SHA256 /tr $TimestampUrl `
        /d "Omni AI Manager" /du "https://github.com/zx90316/Omni-AI-GUI" $ExePath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $signtool verify /pa $ExePath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "==> Verifying release completeness and executable self-test..." -ForegroundColor Cyan
$VerifyParameters = @{
    ReleaseDir = $FinalDir
    ExpectedVersion = $ReleaseVersion
}
if ($CertThumbprint) { $VerifyParameters.RequireSignature = $true }
& (Join-Path $Root "scripts\verify_release.ps1") @VerifyParameters
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}

if (-not $SkipZip) {
    $ZipPath = Join-Path $ReleaseRoot "$ProductName-v$ReleaseVersion-windows-x64-standalone.zip"
    Write-Host "==> Creating release ZIP..." -ForegroundColor Cyan
    Compress-Archive -Path (Join-Path $FinalDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal -Force
    $hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $ChecksumPath = "$ZipPath.sha256"
    Set-Content -LiteralPath $ChecksumPath -Value "$hash  $([IO.Path]::GetFileName($ZipPath))" -Encoding UTF8
    Write-Host "ZIP: $ZipPath" -ForegroundColor Green
    Write-Host "SHA-256: $hash" -ForegroundColor Green
}

Write-Host "Release folder: $FinalDir" -ForegroundColor Green
if (-not $CertThumbprint) {
    Write-Host "Unsigned build: corporate distribution should add Authenticode signing or an IT allowlist." -ForegroundColor Yellow
}
