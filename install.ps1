# Thin entry point. The logic is in install.py, once, so Linux, macOS and
# Windows run the same code rather than three copies that drift apart.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

foreach ($py in @('py', 'python', 'python3')) {
    $found = Get-Command $py -ErrorAction SilentlyContinue
    if ($found) {
        & $found.Source (Join-Path $here 'install.py') @args
        exit $LASTEXITCODE
    }
}

Write-Error 'claude-kit: no python interpreter found (tried py, python, python3). Python 3.11 or newer is required.'
exit 1
