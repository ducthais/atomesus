# Install git pre-push hook for AI log submission (Windows PowerShell).
# Run once after cloning: powershell -ExecutionPolicy Bypass -File scripts\setup_hooks.ps1

$ErrorActionPreference = 'Stop'

$HookFile = '.git/hooks/pre-push'

# Git on Windows runs hooks via Git Bash, so the hook body must be bash.
$HookBody = @'
#!/usr/bin/env bash
# Pre-push: sweep recent Antigravity / Gemini prompts, then submit AI logs.
bash scripts/_pyrun.sh scripts/log_antigravity.py --auto || true
bash scripts/_pyrun.sh scripts/submit_log.py || true
exit 0
'@

# Must be written WITHOUT a BOM: Git Bash reads the first line as the shebang,
# and a leading EF BB BF breaks it ("cannot spawn .git/hooks/pre-push").
# Windows PowerShell 5.1's `-Encoding UTF8` emits a BOM, so write the bytes directly.
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$HookPath = Join-Path (Get-Location) $HookFile
[System.IO.File]::WriteAllText($HookPath, ($HookBody -replace "`r`n", "`n") + "`n", $Utf8NoBom)

# Git for Windows still honours the exec bit when running hooks via MSYS bash.
try { & bash -c "chmod +x '$HookFile' scripts/_pyrun.sh" | Out-Null } catch {}

Write-Host "[ai-log] Git pre-push hook installed."

if (-not (Test-Path .ai-log)) { New-Item -ItemType Directory -Path .ai-log | Out-Null }
if (-not (Test-Path .ai-log/.gitkeep)) { New-Item -ItemType File -Path .ai-log/.gitkeep | Out-Null }

Write-Host "[ai-log] Setup complete. Configure AI_LOG_SERVER in your .env file."
