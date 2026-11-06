param([string]$Distribution = 'Ubuntu-24.04', [string]$LinuxUser = 'minimax')
$ErrorActionPreference = 'Stop'
# Keep WSL alive after the setup terminal exits; no visible helper window.
Start-Process -FilePath wsl.exe -ArgumentList '-d',$Distribution,'-u',$LinuxUser,'--exec','/bin/sleep','infinity' -WindowStyle Hidden
& wsl.exe -d $Distribution -u root --exec /bin/bash -lc 'systemctl start docker dify-grudge-model dify-grudge-memory'
if ($LASTEXITCODE -ne 0) { throw 'Local services have not been installed; follow deploy/README.md first.' }
& wsl.exe -d $Distribution -u root --exec /bin/bash -lc "cd /home/$LinuxUser/.local/share/dify-grudge-shop/dify-upstream/docker && docker compose up -d && docker compose restart nginx"
if ($LASTEXITCODE -ne 0) { throw 'Dify containers failed to start; inspect Docker logs.' }
$checkerWindowsPath = Join-Path $PSScriptRoot 'check_ready.py'
$checkerLinuxPath = & wsl.exe -d $Distribution -u $LinuxUser --exec wslpath -u $checkerWindowsPath
if ($LASTEXITCODE -ne 0) { throw 'Could not locate the HTTP readiness checker inside Ubuntu.' }
& wsl.exe -d $Distribution -u $LinuxUser --exec python3 $checkerLinuxPath.Trim()
if ($LASTEXITCODE -ne 0) { throw 'Dify, model or memory HTTP service is not ready; inspect the local logs.' }
Write-Host 'Dify: http://127.0.0.1:18080'
Write-Host 'Private local administrator credentials: ~/.local/share/dify-grudge-shop/credentials.json (inside Ubuntu)'
