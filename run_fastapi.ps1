$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
python -m uvicorn fast_api_server:app --host 127.0.0.1 --port 8000

