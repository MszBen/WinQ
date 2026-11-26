# Build a release: tests → WinQ.exe (PyInstaller) → smoke test → installer (Inno Setup).
#   powershell -ExecutionPolicy Bypass -File tools\build_release.ps1 [-SkipTests]
# Output: dist\installer\WinQ-<version>-Setup.exe
# Needs: .venv with `pip install -e ".[dev]"`, and Inno Setup 6
#   (winget install JRSoftware.InnoSetup).
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

$version = (Select-String -Path "src\winq\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Building WinQ $version" -ForegroundColor Cyan

if (-not $SkipTests) {
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
}

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist\WinQ) { Remove-Item -Recurse -Force dist\WinQ }
& $py -m PyInstaller winq.spec --noconfirm --log-level WARN
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$proc = Start-Process -FilePath "dist\WinQ\WinQ.exe" -ArgumentList "--smoke-test" -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "Smoke test failed (exit $($proc.ExitCode)): is an audio output available?" }
Write-Host "Smoke test OK" -ForegroundColor Green

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc /Q "/DAppVersion=$version" installer\winq.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$setup = Get-Item "dist\installer\WinQ-$version-Setup.exe"
Write-Host ("Installer: {0} ({1:N1} MB)" -f $setup.FullName, ($setup.Length / 1MB)) -ForegroundColor Green
