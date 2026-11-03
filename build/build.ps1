# Builds dist\DeskSpace-Setup-<version>.exe.
#
# The installer carries the official, PSF-signed embeddable Python plus the app's libraries and
# source. Windows' Smart App Control blocks unsigned exes it hasn't seen before (which includes any
# freshly built PyInstaller exe), but it allows signed Python - so the app runs through that.
# DeskSpace.exe in the install is a renamed copy of the signed pythonw.exe (renaming keeps the signature).
#
# The setup exe itself is unsigned, so Smart App Control may block it too; then install from the
# stage instead:  dist\stage\runtime\python.exe build\install_without_setup.py
#
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File build\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$pyVersion = (& $python -c "import platform; print(platform.python_version())").Trim()
$stage = Join-Path $root "dist\stage"
$runtime = Join-Path $stage "runtime"

Write-Host "== Python $pyVersion embeddable runtime"
$cache = Join-Path $root "build\cache"
New-Item -ItemType Directory -Force $cache | Out-Null
$zip = Join-Path $cache "python-$pyVersion-embed-amd64.zip"
if (-not (Test-Path $zip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip" -OutFile $zip -UseBasicParsing
}
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
Expand-Archive $zip -DestinationPath $runtime
foreach ($exe in "python.exe", "pythonw.exe") {
    $sig = Get-AuthenticodeSignature (Join-Path $runtime $exe)
    if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        throw "$exe is not signed by the Python Software Foundation ($($sig.Status)) - refusing to package it"
    }
}
# Search path for the embedded interpreter: its stdlib, the bundled libraries and the app.
# "import site" makes it process .pth files (pywin32 needs that to find its DLLs).
$pth = Get-ChildItem $runtime -Filter "python*._pth" | Select-Object -First 1
Set-Content $pth.FullName -Encoding ascii -Value @(
    ($pth.BaseName + ".zip"), ".", "Lib\site-packages", "..\app", "import site")
Copy-Item (Join-Path $runtime "pythonw.exe") (Join-Path $runtime "DeskSpace.exe")

Write-Host "== Libraries"
& $python -m pip install --quiet --disable-pip-version-check --no-warn-script-location `
    --target (Join-Path $runtime "Lib\site-packages") -r build\requirements-runtime.txt
if ($LASTEXITCODE) { throw "pip install failed" }

Write-Host "== App"
$app = Join-Path $stage "app"
New-Item -ItemType Directory -Force (Join-Path $app "fences") | Out-Null
Copy-Item deskspace.pyw $app
Copy-Item fences\*.py (Join-Path $app "fences")
& $python build\make_icon.py (Join-Path $stage "deskspace.ico")
if ($LASTEXITCODE) { throw "icon failed" }
Copy-Item (Join-Path $stage "deskspace.ico") build\deskspace.ico

Write-Host "== Self-test with the bundled runtime"
$report = Join-Path $stage "selftest.txt"
$p = Start-Process -FilePath (Join-Path $runtime "DeskSpace.exe") -WorkingDirectory $app `
    -ArgumentList "`"$app\deskspace.pyw`"", "--selftest", "`"$report`"" -Wait -PassThru
Get-Content $report
if ($p.ExitCode -ne 0) { throw "self-test failed" }
Remove-Item $report

Write-Host "== Installer"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found - install it with: winget install JRSoftware.InnoSetup" }
& $iscc /Qp build\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem dist\DeskSpace-Setup-*.exe | ForEach-Object { "{0}  {1:N1} MB" -f $_.FullName, ($_.Length / 1MB) }
