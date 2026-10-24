#!/bin/bash
# Runs on the demo server (called by deploy.ps1). Idempotent: safe to run again on every deploy.
set -euo pipefail
IP="$1"; MODE="${2:-full}"
HOST="${IP//./-}.sslip.io"
APP=/opt/pramaan

echo "== waiting for first-boot setup"
for i in $(seq 1 90); do [ -f /var/log/pramaan-bootstrap-done ] && break; sleep 5; done
[ -f /var/log/pramaan-bootstrap-done ] || { echo "bootstrap not finished"; exit 1; }

echo "== unpacking ($MODE)"
mkdir -p $APP
tar -xzf /tmp/pramaan-deploy.tgz -C $APP
if [ "$MODE" = "full" ]; then
  install -m 600 /tmp/pramaan.env $APP/.env && rm -f /tmp/pramaan.env
  sed -i "s#^PUBLIC_BASE_URL=.*#PUBLIC_BASE_URL=https://$HOST#" $APP/.env
  grep -q '^AWS_PROFILE=' $APP/.env && sed -i 's/^AWS_PROFILE=.*/AWS_PROFILE=/' $APP/.env   # no laptop credentials on the server
fi
chmod 700 $APP/data/keys 2>/dev/null || true
rm -f /tmp/pramaan-deploy.tgz

echo "== python environment"
cd $APP
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu   # CPU wheels for both: no 2 GB of CUDA, and torchvision must match torch
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m playwright install chromium > /dev/null
sudo .venv/bin/python -m playwright install-deps chromium > /dev/null
# Multilingual sentence model for the semantic scam matcher (downloaded once, then used offline)
[ -d models/hf/models--intfloat--multilingual-e5-small ] || HF_HUB_OFFLINE=0 .venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('intfloat/multilingual-e5-small', cache_dir='models/hf', allow_patterns=['*.json','*.txt','model.safetensors','sentencepiece.bpe.model','tokenizer*'])" > /dev/null

echo "== service"
sudo tee /etc/systemd/system/pramaan.service > /dev/null <<EOF
[Unit]
Description=Pramaan (web + Telegram bot)
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
$HOST {
    encode gzip
    request_body {
        max_size 90MB   # per-route limits are enforced by the app: 25 MB public checks, 80 MB issuer video registration
    }
    reverse_proxy 127.0.0.1:8000
}
EOF

sudo systemctl daemon-reload
sudo systemctl enable -q pramaan
sudo systemctl restart pramaan
sudo systemctl reload caddy || sudo systemctl restart caddy

echo "== health"
for i in $(seq 1 40); do curl -fsS http://127.0.0.1:8000/health && break; sleep 3; done
echo
echo "PUBLIC: https://$HOST"
