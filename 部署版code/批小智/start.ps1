[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$Port = 8000,
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$backend = Join-Path $root 'backend'
$python = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).Path } else { Join-Path $root '.backend-venv\Scripts\python.exe' }
$worker = Join-Path $backend 'app\report_export_worker.py'
$logDir = Join-Path $root '.runtime\logs'
$workerProcess = $null
foreach ($required in @($python,$worker,(Join-Path $root 'frontend-dist\index.html'))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "缺少 $required；先执行 .\setup.ps1。" }
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "端口 $Port 已被占用，未启动第二个 SQLite Worker 实例。"
}
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$env:PYTHONPATH = $backend
if (-not $env:APP_ENV) { $env:APP_ENV = 'development' }
if (-not $env:OCR_TOOL_ROOT) {
    $candidate = Join-Path $root '..\..\..\OCR工具测试'
    if (Test-Path -LiteralPath $candidate) { $env:OCR_TOOL_ROOT = (Resolve-Path -LiteralPath $candidate).Path }
}
Push-Location $root
try {
    & $python -m alembic -c (Join-Path $backend 'alembic.ini') upgrade head
    if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败，服务未启动。' }
    $workerProcess = Start-Process -FilePath $python -ArgumentList @($worker) -PassThru `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir 'report.stdout.log') `
        -RedirectStandardError (Join-Path $logDir 'report.stderr.log')
    Write-Host "批小智：http://127.0.0.1:$Port/" -ForegroundColor Green
    Write-Host '首次使用请注册教师账号；按 Ctrl+C 停止。'
    if (-not $env:OCR_TOOL_ROOT) { Write-Warning 'OCR_TOOL_ROOT 未配置，真实 OCR 暂不可用。' }
    & $python -m uvicorn app.main:app --app-dir $backend --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "API 异常退出：$LASTEXITCODE" }
}
finally {
    if ($null -ne $workerProcess) {
        $running = Get-Process -Id $workerProcess.Id -ErrorAction SilentlyContinue
        if ($running) {
            try {
                if ([IO.Path]::GetFullPath($running.Path) -eq [IO.Path]::GetFullPath($python)) {
                    Stop-Process -Id $workerProcess.Id -Force -ErrorAction SilentlyContinue
                }
            }
            catch { Write-Warning '报告 Worker 进程归属未能确认，请手工检查。' }
        }
    }
    Pop-Location
}
