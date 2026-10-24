#!/bin/bash
# First-boot setup for the Pramaan demo server (Ubuntu 24.04). Runs once as root.
set -eux
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv python3-dev build-essential ffmpeg libgl1 git curl debian-keyring debian-archive-keyring apt-transport-https
# Caddy (automatic HTTPS) from its official apt repository
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' -o /etc/apt/sources.list.d/caddy-stable.list
apt-get update -q
apt-get install -y -q caddy
# 4 GB swap: model loading peaks are spiky on an 8 GB machine
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab
timedatectl set-timezone Asia/Kolkata
mkdir -p /opt/pramaan && chown ubuntu:ubuntu /opt/pramaan
touch /var/log/pramaan-bootstrap-done
