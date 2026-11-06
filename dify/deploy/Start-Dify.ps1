param([string]$Distribution = 'Ubuntu-24.04', [string]$LinuxUser = 'minimax')
$ErrorActionPreference = 'Stop'
# Keep WSL alive after the setup terminal exits; no visible helper window.
Start-Process -FilePath wsl.exe -ArgumentList '-d',$Distribution,'-u',$LinuxUser,'--exec','/bin/sleep','infinity' -WindowStyle Hidden
& wsl.exe -d $Distribution -u root --exec /bin/bash -lc 'systemctl start docker dify-grudge-model dify-grudge-memory'
if ($LASTEXITCODE -ne 0) { throw 'Local services have not been installed; follow deploy/README.md first.' }
& wsl.exe -d $Distribution -u root --exec /bin/bash -lc "cd /home/$LinuxUser/.local/share/dify-grudge-shop/dify-upstream/docker && docker compose up -d && docker compose restart nginx"
if ($LASTEXITCODE -ne 0) { throw 'Dify containers failed to start; inspect Docker logs.' }
Write-Host 'Dify: http://127.0.0.1:18080'
Write-Host 'Private local administrator credentials: ~/.local/share/dify-grudge-shop/credentials.json (inside Ubuntu)'
