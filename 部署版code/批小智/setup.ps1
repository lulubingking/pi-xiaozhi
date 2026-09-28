[CmdletBinding()]
param([string]$PythonCommand = 'python')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$venvPython = Join-Path $root '.backend-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $base = (Get-Command $PythonCommand -ErrorAction Stop).Source
    $version = & $base -c 'import sys; print(".".join(map(str,sys.version_info[:2])))'
    if ($LASTEXITCODE -ne 0 -or $version -notin @('3.12','3.13')) {
        throw "需要 Python 3.12 或 3.13，当前为 $version。可通过 -PythonCommand 指定解释器。"
    }
    & $base -m venv (Join-Path $root '.backend-venv')
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败。' }
}
& $venvPython -m pip install -r (Join-Path $root 'backend\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '安装后端依赖失败。' }
Write-Host '环境已安装。运行 .\start.ps1 启动。' -ForegroundColor Green
