param([string]$Database = "teaching-test.sqlite3")
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
$nodeExe = (Get-Command node).Source
if (!(Test-Path -LiteralPath $pythonExe)) { throw '先在仓库根目录创建 .venv 并安装 server/requirements-test.txt。' }
foreach ($port in @(1331,1332)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "端口 $port 已占用；请关闭原测试服务后重试。" }
}
$dataDir = Join-Path $PSScriptRoot 'data'
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$env:NIUMA_DB = Join-Path $dataDir $Database
$env:PYTHONUTF8 = '1'
Remove-Item Env:TEXT_HARNESS_URL -ErrorAction SilentlyContinue
Remove-Item Env:HARNESS_URL -ErrorAction SilentlyContinue
if (!$env:PLAYWRIGHT_EXECUTABLE_PATH) {
    foreach ($candidate in @('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe','C:\Program Files\Google\Chrome\Application\chrome.exe')) {
        if (Test-Path -LiteralPath $candidate) { $env:PLAYWRIGHT_EXECUTABLE_PATH=$candidate; break }
    }
}
$api = Start-Process -FilePath $pythonExe -ArgumentList @('-u',('"'+(Join-Path $PSScriptRoot 'app.py')+'"')) -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataDir 'api.log') -RedirectStandardError (Join-Path $dataDir 'api-error.log')
$vite = Start-Process -FilePath $nodeExe -ArgumentList @(('"'+(Join-Path $repoRoot 'frontend\node_modules\vite\bin\vite.js')+'"'),'--host','127.0.0.1') -WorkingDirectory (Join-Path $repoRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataDir 'web.log') -RedirectStandardError (Join-Path $dataDir 'web-error.log')
@{api=$api.Id;web=$vite.Id;database=$env:NIUMA_DB;root=$repoRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dataDir 'local-processes.json')
Write-Output '测试页面：http://127.0.0.1:1331/#sources'
