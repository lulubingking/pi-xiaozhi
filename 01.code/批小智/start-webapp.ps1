[CmdletBinding()]
param(
    [ValidateSet('preview', 'dev')]
    [string]$FrontendMode = 'preview',
    [switch]$SkipBuild,
    [switch]$SkipDatabaseSetup,
    [switch]$OpenBrowser,
    [ValidateRange(15, 300)]
    [int]$TimeoutSeconds = 60,
    [ValidateRange(1024, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$BackendRoot = Join-Path $ProjectRoot 'backend'
$BackendPython = Join-Path $BackendRoot '.backend-venv\Scripts\python.exe'
$ViteCli = Join-Path $ProjectRoot 'node_modules\vite\bin\vite.js'
$ReportWorker = Join-Path $BackendRoot 'app\report_export_worker.py'
$AlembicConfig = Join-Path $BackendRoot 'alembic.ini'
$SeedScript = Join-Path $BackendRoot 'scripts\seed_dev_teacher.py'
$RuntimeRoot = Join-Path $ProjectRoot '.runtime'
$LogRoot = Join-Path $RuntimeRoot 'logs'
$StatePath = Join-Path $RuntimeRoot 'webapp.json'

$BackendUrl = "http://127.0.0.1:$BackendPort"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"

function Write-Step {
    param([string]$Message)
    Write-Host "[批小智] $Message" -ForegroundColor Cyan
}

function Assert-Path {
    param(
        [string]$Path,
        [string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label 不存在：$Path"
    }
}

function Get-ListeningPids {
    param([int]$Port)
    $pids = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($pids.Count -gt 0) {
        return $pids
    }

    # 某些 Windows 权限/时序下 Get-NetTCPConnection 会暂时看不到新建监听，
    # 用 netstat 做一次只读兜底，避免把“已启动”误判成端口空闲。
    $fallback = @(
        netstat -ano -p tcp 2>$null |
            Select-String -Pattern "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$" |
            ForEach-Object {
                if ($_.Matches.Count -gt 0) {
                    [int]$_.Matches[0].Groups[1].Value
                }
            }
    )
    return @($fallback | Select-Object -Unique)
}

function Test-Http200 {
    param(
        [string]$Url,
        [string]$RequiredText = ''
    )
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        if ([int]$response.StatusCode -ne 200) {
            return $false
        }
        if ($RequiredText -and $response.Content -notmatch [regex]::Escape($RequiredText)) {
            return $false
        }
        return $true
    }
    catch {
        return $false
    }
}

function Wait-Http200 {
    param(
        [string]$Url,
        [string]$Label,
        [int]$Timeout,
        [string]$RequiredText = ''
    )
    $deadline = (Get-Date).AddSeconds($Timeout)
    do {
        if (Test-Http200 -Url $Url -RequiredText $RequiredText) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    throw "$Label 未在 ${Timeout}s 内通过检查：$Url"
}

function Assert-PortAvailable {
    param(
        [int]$Port,
        [string]$Url,
        [string]$Label,
        [string]$RequiredText = ''
    )
    $pids = @(Get-ListeningPids -Port $Port)
    if ($pids.Count -eq 0) {
        return
    }
    if (Test-Http200 -Url $Url -RequiredText $RequiredText) {
        throw "$Label 已经在运行，但不是本次启动器管理的实例。为避免重复启动 Worker，启动器不会自动接管；若确认它是本项目旧实例，请先手动关闭对应 PID 后再重试。端口=$Port PID=$($pids -join ',')"
    }
    $identityHint = ''
    if ($Label -eq '后端') {
        try {
            $openapiResponse = Invoke-WebRequest -UseBasicParsing -Uri "$Url/openapi.json" -TimeoutSec 3
            $openapi = $openapiResponse.Content | ConvertFrom-Json
            $title = if ($openapi.data.info.title) { $openapi.data.info.title } else { $openapi.info.title }
            if ($title) { $identityHint = " 端口上的服务自报名称：$title。" }
        }
        catch {}
    }
    throw "端口 $Port 已被占用，但 $Label 健康检查失败。未自动终止未知进程，请先检查 PID：$($pids -join ',')。$identityHint 可改用 -BackendPort 8001，或在确认归属后手动停止该 PID。"
}

function Get-NodeExecutable {
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($null -eq $node) {
        throw '找不到 node.exe。请先安装 Node.js，并确认 node.exe 已加入 PATH。'
    }
    return $node.Source
}

function Start-ManagedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$StdoutPath,
        [string]$StderrPath
    )
    Write-Step "启动 $Name"
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -WindowStyle Hidden `
        -PassThru
    Start-Sleep -Milliseconds 300
    $observed = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if ($null -eq $observed) {
        throw "$Name 启动后立即退出，请查看日志：$StderrPath"
    }
    return $observed
}

function Get-ProcessRecord {
    param(
        [string]$Name,
        [System.Diagnostics.Process]$Process,
        [string]$ExecutablePath,
        [string]$StdoutPath,
        [string]$StderrPath
    )
    return [ordered]@{
        name = $Name
        pid = [int]$Process.Id
        executable = $ExecutablePath
        startedAt = $Process.StartTime.ToUniversalTime().ToString('o')
        stdout = $StdoutPath
        stderr = $StderrPath
    }
}

function Stop-ProcessRecords {
    param([object[]]$Records)
    foreach ($record in @($Records)) {
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
}

Assert-Path -Path $BackendPython -Label '后端虚拟环境 Python'
Assert-Path -Path $ViteCli -Label 'Vite CLI'
Assert-Path -Path $ReportWorker -Label '报告导出 Worker'
Assert-Path -Path (Join-Path $ProjectRoot 'package.json') -Label '前端 package.json'

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

if (Test-Path -LiteralPath $StatePath) {
    try {
        $existingState = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
        $existingRecords = @($existingState.processes)
        $alive = @($existingRecords | Where-Object { Get-Process -Id ([int]$_.pid) -ErrorAction SilentlyContinue })
        if ($alive.Count -gt 0) {
            if ((Test-Http200 -Url "$BackendUrl/health/ready") -and (Test-Http200 -Url $FrontendUrl -RequiredText '批小智')) {
                throw '启动器管理的服务已经运行。请访问 http://127.0.0.1:5173/，或先执行 .\stop-webapp.ps1。'
            }
            throw '发现上一次启动器的进程仍在运行，但健康检查未通过。请先执行 .\stop-webapp.ps1，再重新启动。'
        }
        Remove-Item -LiteralPath $StatePath -Force
    }
    catch {
        if ($_.Exception.Message -like '启动器管理的服务已经运行*' -or $_.Exception.Message -like '发现上一次启动器*') {
            throw
        }
        Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    }
}

if ($BackendPort -eq $FrontendPort) {
    throw "后端端口和前端端口不能相同：$BackendPort"
}
Assert-PortAvailable -Port $BackendPort -Url "$BackendUrl/health/ready" -Label '后端'
Assert-PortAvailable -Port $FrontendPort -Url $FrontendUrl -Label '前端' -RequiredText '批小智'

$nodeExecutable = Get-NodeExecutable
$startedRecords = New-Object System.Collections.Generic.List[object]

try {
    if (-not $SkipBuild) {
        Write-Step '构建前端 dist（可用 -SkipBuild 跳过）'
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) {
            throw "前端构建失败，退出码=$LASTEXITCODE"
        }
    }

    if (-not $SkipDatabaseSetup) {
        Write-Step '执行 Alembic 数据库迁移'
        & $BackendPython -m alembic -c $AlembicConfig upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "数据库迁移失败，退出码=$LASTEXITCODE"
        }

        Write-Step '初始化开发教师账号'
        & $BackendPython $SeedScript
        if ($LASTEXITCODE -ne 0) {
            throw "开发教师账号初始化失败，退出码=$LASTEXITCODE"
        }
    }

    $backendStdout = Join-Path $LogRoot 'backend.stdout.log'
    $backendStderr = Join-Path $LogRoot 'backend.stderr.log'
    $backendProcess = Start-ManagedProcess `
        -Name 'FastAPI 后端（内置 OCR/批改 Worker）' `
        -FilePath $BackendPython `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--app-dir', $BackendRoot, '--host', '127.0.0.1', '--port', "$BackendPort") `
        -StdoutPath $backendStdout `
        -StderrPath $backendStderr
    $startedRecords.Add((Get-ProcessRecord -Name 'backend' -Process $backendProcess -ExecutablePath $BackendPython -StdoutPath $backendStdout -StderrPath $backendStderr))
    Wait-Http200 -Url "$BackendUrl/health/ready" -Label '后端 ready' -Timeout $TimeoutSeconds

    # Windows venv 的 Python redirector 可能让实际 Uvicorn 进程使用另一个 PID。
    # 启动前已确认后端端口空闲，因此此处监听 PID 都属于本次后端启动链，必须一并记录。
    foreach ($listenerPid in @(Get-ListeningPids -Port $BackendPort)) {
        if ([int]$listenerPid -eq [int]$backendProcess.Id) {
            continue
        }
        $runtimeProcess = Get-Process -Id ([int]$listenerPid) -ErrorAction SilentlyContinue
        if ($null -ne $runtimeProcess) {
            $startedRecords.Add((Get-ProcessRecord -Name 'backend-runtime' -Process $runtimeProcess -ExecutablePath $runtimeProcess.Path -StdoutPath $backendStdout -StderrPath $backendStderr))
        }
    }

    $workerStdout = Join-Path $LogRoot 'report-export-worker.stdout.log'
    $workerStderr = Join-Path $LogRoot 'report-export-worker.stderr.log'
    $workerProcess = Start-ManagedProcess `
        -Name '报告导出 Worker' `
        -FilePath $BackendPython `
        -ArgumentList @($ReportWorker) `
        -StdoutPath $workerStdout `
        -StderrPath $workerStderr
    $startedRecords.Add((Get-ProcessRecord -Name 'report-export-worker' -Process $workerProcess -ExecutablePath $BackendPython -StdoutPath $workerStdout -StderrPath $workerStderr))

    $frontendStdout = Join-Path $LogRoot "frontend-$FrontendMode.stdout.log"
    $frontendStderr = Join-Path $LogRoot "frontend-$FrontendMode.stderr.log"
    $env:VITE_API_PROXY_TARGET = $BackendUrl
    if ($FrontendMode -eq 'preview') {
        $frontendArguments = @($ViteCli, 'preview', '--host', '127.0.0.1', '--port', "$FrontendPort")
    }
    else {
        $frontendArguments = @($ViteCli, '--host', '127.0.0.1', '--port', "$FrontendPort")
    }
    $frontendProcess = Start-ManagedProcess `
        -Name "Vite 前端（$FrontendMode）" `
        -FilePath $nodeExecutable `
        -ArgumentList $frontendArguments `
        -StdoutPath $frontendStdout `
        -StderrPath $frontendStderr
    $startedRecords.Add((Get-ProcessRecord -Name 'frontend' -Process $frontendProcess -ExecutablePath $nodeExecutable -StdoutPath $frontendStdout -StderrPath $frontendStderr))
    Wait-Http200 -Url $FrontendUrl -Label '前端首页' -Timeout $TimeoutSeconds -RequiredText '批小智'

    $proxyResponse = $null
    try {
        $proxyResponse = Invoke-WebRequest -UseBasicParsing -Uri "$FrontendUrl/api/v1/classes" -TimeoutSec 5
    }
    catch {
        if ($_.Exception.Response) {
            $proxyResponse = $_.Exception.Response
        }
    }
    if ($null -eq $proxyResponse -or [int]$proxyResponse.StatusCode -notin @(200, 401, 403)) {
        throw '前端 /api 代理检查失败；前端首页虽然可访问，但后端代理未返回预期响应。'
    }

    $state = [ordered]@{
        version = 1
        projectRoot = $ProjectRoot
        frontendMode = $FrontendMode
        startedAt = (Get-Date).ToUniversalTime().ToString('o')
        urls = [ordered]@{
            frontend = $FrontendUrl
            backend = $BackendUrl
        }
        processes = $startedRecords.ToArray()
    }
    $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatePath -Encoding UTF8

    Write-Host ''
    Write-Host '批小智已启动并通过健康检查。' -ForegroundColor Green
    Write-Host "前端：$FrontendUrl"
    Write-Host "后端：$BackendUrl/health/ready"
    Write-Host "日志：$LogRoot"
    Write-Host '停止：.\stop-webapp.ps1'
    if ($OpenBrowser) {
        Start-Process $FrontendUrl | Out-Null
    }
}
catch {
    $startupError = $_
    if ($startedRecords.Count -gt 0) {
        Write-Warning '启动未完成，正在清理本次已启动的进程。'
        try {
            Stop-ProcessRecords -Records $startedRecords.ToArray()
        }
        catch {
            Write-Warning "清理启动进程时出现附加错误：$($_.Exception.Message)"
        }
    }
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    throw $startupError
}
