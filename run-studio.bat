@echo off
setlocal
set "REPO_ROOT=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%scripts\preflight.ps1"
if errorlevel 1 exit /b %errorlevel%

powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $repo = (Resolve-Path -LiteralPath '%REPO_ROOT%').Path; $venvPython = Join-Path $repo '.venv\Scripts\python.exe'; $python = 'py'; $apiPrefix = @('-3.12'); if (Test-Path -LiteralPath $venvPython) { $version = & $venvPython -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))'; if ($LASTEXITCODE -eq 0 -and $version.Trim() -eq '3.12') { $python = $venvPython; $apiPrefix = @() } }; $apiArgs = $apiPrefix + @('-m','uvicorn','app.main:app','--app-dir',(Join-Path $repo 'backend'),'--host','127.0.0.1','--port','8765'); Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList $apiArgs; $workerArgs = $apiPrefix + @('-m','app.worker'); Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList $workerArgs -WorkingDirectory (Join-Path $repo 'backend'); $deadline = (Get-Date).AddSeconds(30); do { try { $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health/ready' -TimeoutSec 1; if ($ready.status -eq 'ready') { Start-Process 'http://127.0.0.1:8765'; exit 0 } } catch { Start-Sleep -Milliseconds 500 } } while ((Get-Date) -lt $deadline); throw 'Studio API did not become ready within 30 seconds.' }"
exit /b %errorlevel%
