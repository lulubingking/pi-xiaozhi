[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$RuntimeRoot = Join-Path $ProjectRoot '.runtime'
$StatePath = Join-Path $RuntimeRoot 'webapp.json'

function Write-Step {
    param([string]$Message)
    Write-Host "[批小智] $Message" -ForegroundColor Cyan
}

if (-not (Test-Path -LiteralPath $StatePath)) {
    Write-Host '没有找到启动器状态文件，未终止任何进程。' -ForegroundColor Yellow
    Write-Host '如果端口仍被占用，请先用 netstat -ano 检查 PID，不要直接终止未知进程。'
    exit 0
}

$state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
foreach ($record in @($state.processes)) {
    $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        continue
    }

    $sameExecutable = $false
    try {
        $sameExecutable = ([IO.Path]::GetFullPath($process.Path) -eq [IO.Path]::GetFullPath([string]$record.executable))
    }
    catch {
        $sameExecutable = $false
    }
    if ($sameExecutable) {
        Write-Step "停止 $($record.name) PID=$($record.pid)"
        Stop-Process -Id ([int]$record.pid) -Force -ErrorAction SilentlyContinue
    }
    else {
        Write-Warning "跳过未能确认归属的 PID=$($record.pid)，不会终止未知进程。"
    }
}

Start-Sleep -Seconds 1
Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
Write-Host '批小智已停止；日志保留在 .runtime\logs。' -ForegroundColor Green
