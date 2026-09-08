$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ProjectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $ProjectPython)) {
    py -3 -m venv .venv
    & $ProjectPython -m pip install --upgrade pip
    & $ProjectPython -m pip install -r requirements.txt
}

& $ProjectPython main.py
exit $LASTEXITCODE
