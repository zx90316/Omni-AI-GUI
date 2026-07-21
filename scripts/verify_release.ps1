#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)][string]$ReleaseDir,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ResolvedRelease = (Resolve-Path -LiteralPath $ReleaseDir).Path
$Manifest = Get-Content -LiteralPath (Join-Path $Root "packaging\release-manifest.json") `
    -Raw -Encoding UTF8 | ConvertFrom-Json

$errors = [Collections.Generic.List[string]]::new()

function Read-SelfTestResult {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        $errors.Add("$Label wrote invalid JSON: $($_.Exception.Message)")
        return $null
    }
}

function Format-SelfTestDetails {
    param($Result)
    if ($null -eq $Result -or $null -eq $Result.missing) {
        return ""
    }
    $details = @($Result.missing) |
        ForEach-Object { "$($_)".Trim() } |
        Where-Object { $_ }
    if ($details.Count -eq 0) { return "" }
    return ": $($details -join '; ')"
}
foreach ($relative in $Manifest.required_paths) {
    if (-not (Test-Path -LiteralPath (Join-Path $ResolvedRelease $relative))) {
        $errors.Add("Missing required release path: $relative")
    }
}

Get-ChildItem -LiteralPath $ResolvedRelease -Recurse -Force | ForEach-Object {
    $relative = $_.FullName.Substring($ResolvedRelease.Length).TrimStart('\', '/')
    $parts = $relative -split '[\\/]'
    foreach ($part in $parts) {
        if ($Manifest.excluded_names -contains $part) {
            $errors.Add("Forbidden release path: $relative")
            break
        }
    }
    if (-not $_.PSIsContainer) {
        $extension = [IO.Path]::GetExtension($_.Name).ToLowerInvariant()
        if ($Manifest.excluded_extensions -contains $extension) {
            $errors.Add("Forbidden release file type: $relative")
        }
    }
}

$VersionFile = Join-Path $ResolvedRelease "omni_version.py"
if (Test-Path -LiteralPath $VersionFile) {
    $versionText = Get-Content -LiteralPath $VersionFile -Raw -Encoding UTF8
    if ($versionText -notmatch ('__version__\s*=\s*["'']' + [regex]::Escape($ExpectedVersion) + '["'']')) {
        $errors.Add("Release source version does not match $ExpectedVersion.")
    }
}

$ExePath = Join-Path $ResolvedRelease "Omni-AI-Manager.exe"
if ($RequireSignature -and (Test-Path -LiteralPath $ExePath)) {
    $signature = Get-AuthenticodeSignature -LiteralPath $ExePath
    if ($signature.Status -ne "Valid") {
        $errors.Add("Authenticode signature is not valid: $($signature.Status)")
    }
}

if ($errors.Count -eq 0) {
    $selfTestOutput = Join-Path ([IO.Path]::GetTempPath()) ("omni-release-" + [guid]::NewGuid().ToString("N") + ".json")
    try {
        $process = Start-Process -FilePath $ExePath `
            -ArgumentList @("--verify-install", $selfTestOutput) `
            -WorkingDirectory $ResolvedRelease `
            -WindowStyle Hidden `
            -PassThru
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            $errors.Add("Executable self-test timed out after 30 seconds.")
        } else {
            $result = Read-SelfTestResult -Path $selfTestOutput -Label "Executable self-test"
            if ($null -eq $result) {
                if (-not (Test-Path -LiteralPath $selfTestOutput)) {
                    $errors.Add("Executable self-test did not write its result (exit code $($process.ExitCode)).")
                }
            } else {
                $details = Format-SelfTestDetails -Result $result
                if ($process.ExitCode -ne 0) {
                    $errors.Add("Executable self-test failed with exit code $($process.ExitCode)$details.")
                } elseif ($result.status -ne "ok") {
                    $errors.Add("Executable reported an incomplete installation$details.")
                }
                if ($result.version -ne $ExpectedVersion) {
                    $errors.Add("Executable version '$($result.version)' does not match '$ExpectedVersion'.")
                }
            }
        }
    } finally {
        Remove-Item -LiteralPath $selfTestOutput -Force -ErrorAction SilentlyContinue
    }
}

if ($errors.Count -eq 0) {
    $projectTestOutput = Join-Path ([IO.Path]::GetTempPath()) ("omni-project-" + [guid]::NewGuid().ToString("N") + ".json")
    try {
        $process = Start-Process -FilePath $ExePath `
            -ArgumentList @(
                "--verify-install",
                "`"$projectTestOutput`"",
                "--project-dir",
                "`"$Root`""
            ) `
            -WorkingDirectory $ResolvedRelease `
            -WindowStyle Hidden `
            -PassThru
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            $errors.Add("External project import test timed out after 30 seconds.")
        } else {
            $result = Read-SelfTestResult -Path $projectTestOutput -Label "External project import test"
            if ($null -eq $result) {
                if (-not (Test-Path -LiteralPath $projectTestOutput)) {
                    $errors.Add("External project import test did not write its result (exit code $($process.ExitCode)).")
                }
            } else {
                $details = Format-SelfTestDetails -Result $result
                if ($process.ExitCode -ne 0) {
                    $errors.Add("External project import test failed with exit code $($process.ExitCode)$details.")
                } elseif ($result.status -ne "ok") {
                    $errors.Add("Manager could not import the external Backend project support modules$details.")
                }
                if ($result.version -ne $ExpectedVersion) {
                    $errors.Add("External project test reported version '$($result.version)'.")
                }
            }
        }
    } finally {
        Remove-Item -LiteralPath $projectTestOutput -Force -ErrorAction SilentlyContinue
    }
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Host "Release verification passed: $ResolvedRelease" -ForegroundColor Green
