# Install project git hooks into .git/hooks (Windows / PowerShell).
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $PSScriptRoot 'git-hooks\prepare-commit-msg'
$DestDir = Join-Path $Root '.git\hooks'
$Dest = Join-Path $DestDir 'prepare-commit-msg'

if (-not (Test-Path (Join-Path $Root '.git'))) {
    Write-Error 'Not a git repository (no .git directory).'
}

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

$wrapper = @"
#!/bin/sh
exec python "$Root/scripts/git-hooks/prepare-commit-msg" "`$1" "`$2" "`$3"
"@
Set-Content -Path $Dest -Value $wrapper -Encoding UTF8

Write-Host "Installed prepare-commit-msg -> $Dest"
