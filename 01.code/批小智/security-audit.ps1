[CmdletBinding()]
param(
    [switch]$CheckRuntime,
    [ValidateRange(1024, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
Set-Location -LiteralPath $ProjectRoot

$findings = New-Object System.Collections.Generic.List[string]
function Finding {
    param([string]$Kind, [string]$Path, [int]$Line = 0)
    $suffix = if ($Line -gt 0) { ":$Line" } else { '' }
    $findings.Add("$Kind`t$Path$suffix")
}

$excluded = '\\(node_modules|\.git|\.backend-venv|\.venv[^\\]*|\.conda[^\\]*|__pycache__|models|数据集|handwriting_benchmark\\data|dist)\\'
$textExtensions = @('.ps1', '.psm1', '.py', '.ts', '.tsx', '.js', '.cjs', '.json', '.yaml', '.yml', '.toml', '.ini', '.env', '.md', '.txt', '.html', '.css', '.log', '.sql', '.xml', '.conf')
$secretPatterns = @(
    '(?i)\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b',
    '\bAKIA[0-9A-Z]{16}\b',
    '(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----',
    '(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*["''][^"'']{16,}["'']',
    '^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD)\s*[:=]\s*[^\s,"''<>]{16,}'
)

Write-Host '== 批小智安全审计 ==' -ForegroundColor Cyan
Write-Host "根目录: $ProjectRoot"

# Never print secret contents. Report only file paths and line numbers.
$rg = (Get-Command rg.exe -ErrorAction SilentlyContinue).Source
if (-not $rg) { throw '找不到 rg.exe；请安装 ripgrep 后再运行安全审计。' }
$relativeFiles = @(& $rg --files --hidden --no-ignore-vcs `
    -g '!node_modules/**' -g '!backend/.backend-venv/**' -g '!OCR工具测试/.conda*/**' `
    -g '!OCR工具测试/.venv*/**' -g '!OCR工具测试/models/**' -g '!数据集/**' `
    -g '!handwriting_benchmark/**' -g '!.git/**' 2>$null)
$files = New-Object System.Collections.Generic.List[IO.FileInfo]
foreach ($relative in $relativeFiles) {
    $candidate = Get-Item -LiteralPath (Join-Path $ProjectRoot $relative) -Force -ErrorAction SilentlyContinue
    if ($candidate -and -not $candidate.PSIsContainer -and $candidate.Length -le 50000000) {
        $extension = $candidate.Extension.ToLowerInvariant()
        if ($textExtensions -contains $extension -or $extension -in @('.db', '.sqlite', '.sqlite3')) {
            $files.Add($candidate)
        }
    }
}
$localSecretPath = Join-Path $ProjectRoot 'OCR工具测试\.env.local'
if (Test-Path -LiteralPath $localSecretPath) {
    $localSecretItem = Get-Item -LiteralPath $localSecretPath -Force
    if (-not ($files.FullName -contains $localSecretItem.FullName)) { $files.Add($localSecretItem) }
}

foreach ($file in $files) {
    if ($file.Name -match '^(\.env|\.env\..*)$' -and $file.Name -notmatch '\.example$') {
        Finding 'LOCAL_SECRET_FILE' $file.FullName
    }
    if ($textExtensions -contains $file.Extension.ToLowerInvariant()) {
        try {
            $lineNo = 0
            foreach ($line in [IO.File]::ReadLines($file.FullName)) {
                $lineNo++
                for ($patternIndex = 0; $patternIndex -lt $secretPatterns.Count; $patternIndex++) {
                    $pattern = $secretPatterns[$patternIndex]
                    $matched = if ($patternIndex -eq 4) { $line -cmatch $pattern } else { $line -match $pattern }
                    if ($matched) {
                        $isPlaceholder = $line -match '(?i)(placeholder|example|your_[a-z_]+_here|redacted|dummy|test-only|fake)'
                        if (-not $isPlaceholder) { Finding 'SECRET_LIKE_TEXT' $file.FullName $lineNo }
                        break
                    }
                }
            }
        } catch {
            if ($file.FullName -notmatch '\\.runtime\\logs\\') {
                Finding 'UNREADABLE_TEXT_FILE' $file.FullName
            }
        }
    }
}

if (Test-Path -LiteralPath $localSecretPath) {
    $secretLine = Get-Content -LiteralPath $localSecretPath | Where-Object { $_ -match '^DEEPSEEK_API_KEY=' } | Select-Object -First 1
    if ($secretLine) {
        $secret = $secretLine.Substring('DEEPSEEK_API_KEY='.Length)
        $secretBytes = [Text.Encoding]::UTF8.GetBytes($secret)
        $exactMatches = 0
        foreach ($file in $files) {
            try {
                $bytes = [IO.File]::ReadAllBytes($file.FullName)
                if ($bytes.Length -lt $secretBytes.Length) { continue }
                for ($i = 0; $i -le $bytes.Length - $secretBytes.Length; $i++) {
                    $same = $true
                    for ($j = 0; $j -lt $secretBytes.Length; $j++) {
                        if ($bytes[$i + $j] -ne $secretBytes[$j]) { $same = $false; break }
                    }
                    if ($same) { Finding 'EXACT_LOCAL_KEY_COPY' $file.FullName; $exactMatches++; break }
                }
            } catch {}
        }
        Write-Host "本地 Key 精确副本数量（包含 .env.local 自身）: $exactMatches"
    }
}

if ($CheckRuntime) {
    foreach ($url in @("http://127.0.0.1:$BackendPort/health/ready", "http://127.0.0.1:$FrontendPort/")) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
            Write-Host "运行检查通过: $url [$($response.StatusCode)]"
            if ($response.Content -match '(?i)Bearer\s+[^\s<]+|\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b') {
                Finding 'RUNTIME_SECRET_LIKE_RESPONSE' $url
            }
        } catch {
            Finding 'RUNTIME_UNAVAILABLE' $url
        }
    }
    try {
        $apiHeaders = (& curl.exe -sS -D - -o NUL "http://127.0.0.1:$BackendPort/api/v1/settings" 2>$null) -join "`n"
        if ($apiHeaders -notmatch '(?im)^HTTP/[^\r\n]+\s+401\b' -or
            $apiHeaders -notmatch '(?im)^x-content-type-options:\s*nosniff' -or
            $apiHeaders -notmatch '(?im)^cache-control:\s*no-store') {
            Finding 'RUNTIME_API_SECURITY_HEADERS_MISSING' "http://127.0.0.1:$BackendPort/api/v1/settings"
        }
    } catch {
        Finding 'RUNTIME_UNAVAILABLE' "http://127.0.0.1:$BackendPort/api/v1/settings"
    }
}

Write-Host "扫描完成；发现数: $($findings.Count)"
foreach ($finding in $findings | Select-Object -Unique) { Write-Host $finding }
if ($findings.Count -gt 0) { exit 2 }
exit 0
