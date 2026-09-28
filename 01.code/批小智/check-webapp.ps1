[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

$StatePath = Join-Path $PSScriptRoot '.runtime\webapp.json'
$FrontendUrl = 'http://127.0.0.1:5173'
$BackendUrl = 'http://127.0.0.1:8000'
if (Test-Path -LiteralPath $StatePath) {
    try {
        $stateHint = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
        if ($stateHint.urls.frontend) { $FrontendUrl = [string]$stateHint.urls.frontend }
        if ($stateHint.urls.backend) { $BackendUrl = [string]$stateHint.urls.backend }
    }
    catch {}
}

function Test-Endpoint {
    param([string]$Name, [string]$Url, [string]$RequiredText = '')
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        $ok = ([int]$response.StatusCode -eq 200)
        if ($RequiredText -and $response.Content -notmatch [regex]::Escape($RequiredText)) {
            $ok = $false
        }
        if ($ok) {
            Write-Host "PASS  $Name  $Url" -ForegroundColor Green
            return $true
        }
        Write-Host "FAIL  $Name  HTTP $($response.StatusCode)" -ForegroundColor Red
        return $false
    }
    catch {
        Write-Host "FAIL  $Name  $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

$backendOk = Test-Endpoint -Name '后端 ready' -Url "$BackendUrl/health/ready"
$frontendOk = Test-Endpoint -Name '前端首页' -Url $FrontendUrl -RequiredText '批小智'

try {
    $proxy = Invoke-WebRequest -UseBasicParsing -Uri "$FrontendUrl/api/v1/classes" -TimeoutSec 5
    $proxyCode = [int]$proxy.StatusCode
}
catch {
    if ($_.Exception.Response) {
        $proxyCode = [int]$_.Exception.Response.StatusCode
    }
    else {
        $proxyCode = 0
    }
}
if ($proxyCode -in @(200, 401, 403)) {
    Write-Host "PASS  前端 /api 代理  HTTP $proxyCode" -ForegroundColor Green
    $proxyOk = $true
}
else {
    Write-Host "FAIL  前端 /api 代理  HTTP $proxyCode" -ForegroundColor Red
    $proxyOk = $false
}

if (Test-Path -LiteralPath $StatePath) {
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    Write-Host "管理状态：$StatePath"
    foreach ($record in @($state.processes)) {
        if (Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue) {
            Write-Host "RUN   $($record.name) PID=$($record.pid)"
        }
        else {
            Write-Host "STOP  $($record.name) PID=$($record.pid)" -ForegroundColor Yellow
        }
    }
}
else {
    Write-Host '没有启动器状态文件；服务可能是手工启动或不是本项目服务。' -ForegroundColor Yellow
}

if ($backendOk -and $frontendOk -and $proxyOk) {
    exit 0
}
exit 1
