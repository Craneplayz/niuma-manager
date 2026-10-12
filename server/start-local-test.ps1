param(
    [ValidateSet('development','test')][string]$Environment = 'test',
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (!$Python) { $Python = Join-Path $repoRoot '.venv/Scripts/python.exe' }
if (!(Test-Path -LiteralPath $Python)) { throw '请创建根目录 .venv 并安装 server/requirements-test.txt，或通过 -Python 指定解释器。' }
$manager = Join-Path $PSScriptRoot 'manage_environment.py'
& $Python $manager init $Environment
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# Foreground parent keeps both children together; Ctrl+C stops this environment.
& $Python $manager local $Environment
exit $LASTEXITCODE
