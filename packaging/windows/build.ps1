# Build Archy for Windows in one go: venv, PyInstaller, self-test, installer.
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#       -> packaging\windows\Output\Archy-<version>-setup.exe
#   ... -Publish   also adds it to the GitLab release (needs $env:GITLAB_TOKEN,
#                  a personal access token with the api scope)
#
# Needs Python 3.11+ (the py launcher from python.org). Installs the project +
# PyInstaller into .venv, and Inno Setup with winget, when missing.
param([switch]$Publish)
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed ($LASTEXITCODE)" } }

$version = (Select-String -Path src\archy\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$python = ".venv\Scripts\python.exe"

Write-Host ">> Setup"
if (-not (Test-Path $python)) {
    $wanted = if ((py --list) -match "3\.12") { "-3.12" } else { "-3" }  # 3.12 like CI
    py $wanted -m venv .venv; Check "Creating .venv"
}
& $python -m pip install -q --upgrade pip; Check "pip"
& $python -m pip install -q -e ".[dev]" pyinstaller; Check "Installing Archy"

function Find-Iscc {
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($dir in "${env:ProgramFiles(x86)}", $env:ProgramFiles, "$env:LOCALAPPDATA\Programs") {
        $path = Join-Path $dir "Inno Setup 6\ISCC.exe"
        if (Test-Path $path) { return $path }
    }
}
$iscc = Find-Iscc
if (-not $iscc) {
    winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
    $iscc = Find-Iscc
    if (-not $iscc) { throw "Inno Setup not found; install it from https://jrsoftware.org/isinfo.php" }
}

Write-Host ">> Build"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $python -m PyInstaller packaging\archy.spec --noconfirm; Check "PyInstaller"

Write-Host ">> Self-test"
$test = Start-Process dist\Archy\Archy.exe -ArgumentList "--self-test" -NoNewWindow -Wait -PassThru
if ($test.ExitCode -ne 0) { throw "Self-test of the build failed ($($test.ExitCode))" }

Write-Host ">> Installer"
& $iscc /Q "/DAppVersion=$version" packaging\windows\archy.iss; Check "Inno Setup"
$setup = "packaging\windows\Output\Archy-$version-setup.exe"
Write-Host ">> Done: $setup"

if ($Publish) { & $python scripts\publish_build.py $setup; Check "Publishing" }
