[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 8000)

$base = "http://127.0.0.1:$Port"
$ok = $true
foreach ($check in @(@('网页','/','批小智'),@('数据库和存储','/health/ready','ready'))) {
    try {
        $response = Invoke-WebRequest -Uri ($base+$check[1]) -UseBasicParsing -TimeoutSec 5
        $passed = $response.StatusCode -eq 200 -and $response.Content.Contains($check[2])
    }
    catch { $passed = $false }
    if ($passed) { Write-Host "PASS $($check[0])" -ForegroundColor Green }
    else { Write-Host "FAIL $($check[0])" -ForegroundColor Red; $ok = $false }
}
try {
    Invoke-WebRequest -Uri ($base+'/api/v1/classes') -UseBasicParsing -TimeoutSec 5 | Out-Null
    $code = 200
}
catch { $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 } }
if ($code -in @(200,401,403)) { Write-Host "PASS API 路由 ($code)" -ForegroundColor Green }
else { Write-Host "FAIL API 路由 ($code)" -ForegroundColor Red; $ok = $false }
if (-not $ok) { exit 1 }
