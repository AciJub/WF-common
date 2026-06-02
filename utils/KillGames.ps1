Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location "E:\OneDrive\WF\common\utils"

$env:DICTAPI_URL = "http://localhost:8788"
$env:DICTAPI_KEY = "change-me"

$env:PASSOUT_API_TIMEOUT_S = "30"
$env:PASSOUT_LOGIN_TIMEOUT_S = "12"
$env:PASSOUT_WAIT_POLL_S = "0.25"

python killgames.py