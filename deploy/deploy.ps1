# Deploy Pramaan to the demo server. Run from anywhere:  powershell -File deploy\deploy.ps1 [-Ip <server ip>] [-CodeOnly]
# Ships code + the data the server needs. Never ships: raw originals (GPS), consent forms, local DB/vault/store, .venv, models.
param([string]$Ip = "13.207.22.33", [switch]$CodeOnly)
$ErrorActionPreference = "Stop"
$app = Split-Path $PSScriptRoot -Parent
$key = "$env:USERPROFILE\.ssh\pramaan-demo.pem"
$ssh = @("-i", $key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15")
$tgz = Join-Path $env:TEMP "pramaan-deploy.tgz"

Push-Location $app
try {
    $items = @("app", "eval", "tools", "tests", "docs", "deploy", "requirements.txt", "README.md", "ACKNOWLEDGEMENTS.md", "BUILD_LOG.md", ".env.example")
    if (-not $CodeOnly) { $items += @("data/index", "data/cache", "data/keys", "data/seals", "data/media/genuine", "data/media/test") }
    $items = @($items | Where-Object { if (Test-Path $_) { $true } else { Write-Host "skipping missing $_"; $false } })
    tar --exclude="__pycache__" --exclude="*.pyc" --exclude="app/static/_card_*" -czf $tgz @items
} finally { Pop-Location }
"{0:N1} MB bundle" -f ((Get-Item $tgz).Length / 1MB)

scp @ssh $tgz "ubuntu@${Ip}:/tmp/pramaan-deploy.tgz"
if (-not $CodeOnly) {
    # .env goes over SSH only; the server's public URL replaces the laptop's
    scp @ssh (Join-Path $app ".env") "ubuntu@${Ip}:/tmp/pramaan.env"
}
scp @ssh (Join-Path $PSScriptRoot "server-setup.sh") "ubuntu@${Ip}:/tmp/server-setup.sh"
ssh @ssh "ubuntu@$Ip" "bash /tmp/server-setup.sh $Ip $(if ($CodeOnly) { 'code' } else { 'full' })"
