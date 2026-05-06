#!/usr/bin/env bash
# bug-fix-2305 / spec/25 — 阿里云 ECS 一次性 setup
#
# 适配场景: Ubuntu 22.04, 2核/2G/40G ESSD, 公网 IP, 全新机器
# 用法 (服务器上 root 跑):
#   wget https://raw.githubusercontent.com/<your-org>/<repo>/main/xgzh/infra/server-setup.sh
#   chmod +x server-setup.sh
#   sudo bash server-setup.sh
#
# 这脚本做的事 (~5-8 min):
#   1. 装 Docker + docker-compose-plugin (官方仓库)
#   2. 配 1G swap (兜 OOM, 平时不用)
#   3. 装 ufw + 配防火墙 (22/80/443/8000)
#   4. 装 fail2ban (反 SSH 暴力破解)
#   5. 创建 deployer 用户 (不用 root SSH)
#   6. 创建 /opt/xgzh + 拉本仓库的 production compose / env example
#   7. PG/Redis 数据目录 + 权限
#
# 跑完后用户接续:
#   8. 把 deployer 用户公钥写进 /home/deployer/.ssh/authorized_keys
#   9. cd /opt/xgzh && cp .env.production.example .env && vi .env (填密码 + LLM key)
#   10. docker compose -f docker-compose.production.yml up -d postgres redis
#   11. (首次镜像 push 后) docker compose -f docker-compose.production.yml up -d xgzh-api

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err() { echo -e "${RED}[err]${NC} $*" >&2; }

if [ "$(id -u)" -ne 0 ]; then
  err "This script must be run as root (sudo bash server-setup.sh)"
  exit 1
fi

# ============================================================
# Step 1: 系统更新 + 基础工具
# ============================================================
log "Step 1/7: 更新系统 + 装基础工具..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  curl wget vim git \
  ca-certificates gnupg lsb-release \
  ufw fail2ban htop \
  jq

# ============================================================
# Step 2: 装 Docker + docker-compose-plugin
# ============================================================
log "Step 2/7: 装 Docker + compose plugin..."

if command -v docker &>/dev/null; then
  warn "Docker 已装, 跳过 (version: $(docker --version))"
else
  # 用阿里云 Docker mirror (国内拉镜像快)
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  # shellcheck disable=SC1091  # /etc/os-release 在 Ubuntu 22.04 服务器一定存在
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  systemctl enable docker
  systemctl start docker
fi

# Docker 镜像加速 (国内拉 hub.docker.com 镜像)
log "    配 Docker registry mirror (阿里云加速)..."
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://hub-mirror.c.163.com"
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
systemctl restart docker

# ============================================================
# Step 3: 配 1GB swap (2G ECS 兜 OOM)
# ============================================================
log "Step 3/7: 配 1GB swap (兜 OOM)..."
if [ -f /swapfile ]; then
  warn "swap 已配, 跳过"
else
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  # 调低 swappiness, 避免 PG 频繁 swap
  sysctl vm.swappiness=10 >/dev/null
  echo 'vm.swappiness=10' >> /etc/sysctl.conf
fi

# ============================================================
# Step 4: 配 ufw 防火墙
# ============================================================
log "Step 4/7: 配 ufw 防火墙 (放 22/80/443/8000)..."
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw allow 8000/tcp comment 'xgzh-api (TODO: 上线后藏 Nginx 后面, 关 8000 公网)'
ufw --force enable
ufw status verbose

# ============================================================
# Step 5: 配 fail2ban (反 SSH 暴力破解)
# ============================================================
log "Step 5/7: 配 fail2ban (反 SSH 暴力破解)..."
cat > /etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 5
findtime = 600
bantime = 3600
EOF
systemctl enable fail2ban
systemctl restart fail2ban

# ============================================================
# Step 6: 创建 deployer 用户 + sudo + docker
# ============================================================
log "Step 6/7: 创建 deployer 用户..."
if id deployer &>/dev/null; then
  warn "deployer 用户已存在, 跳过创建"
else
  useradd -m -s /bin/bash deployer
  usermod -aG docker deployer
  # 不给 sudo (deploy 不需要 sudo)
fi
mkdir -p /home/deployer/.ssh
chmod 700 /home/deployer/.ssh
chown -R deployer:deployer /home/deployer/.ssh
log "    deployer 用户已就绪. ⚠️ 后续手动操作:"
log "    1. 把 GitHub Secrets DEPLOY_SSH_KEY 对应的公钥, 写入 /home/deployer/.ssh/authorized_keys"
log "       echo '<your-public-key>' >> /home/deployer/.ssh/authorized_keys"
log "       chmod 600 /home/deployer/.ssh/authorized_keys"
log "       chown deployer:deployer /home/deployer/.ssh/authorized_keys"

# ============================================================
# Step 7: 部署目录 + 配置文件
# ============================================================
log "Step 7/7: 创建 /opt/xgzh + PG/Redis 数据目录..."
mkdir -p /opt/xgzh/data/postgres
mkdir -p /opt/xgzh/data/redis
chown -R deployer:deployer /opt/xgzh

if [ ! -f /opt/xgzh/docker-compose.production.yml ]; then
  warn "docker-compose.production.yml 不存在, 请手动从仓库拷贝:"
  warn "    sudo -u deployer wget https://raw.githubusercontent.com/<your-org>/<repo>/main/xgzh/infra/docker-compose.production.yml -O /opt/xgzh/docker-compose.production.yml"
  warn "    sudo -u deployer wget https://raw.githubusercontent.com/<your-org>/<repo>/main/xgzh/infra/.env.production.example -O /opt/xgzh/.env.production.example"
fi

# ============================================================
# 完成 + 后续指引
# ============================================================
echo ""
echo "================================================================"
log "✅ 服务器 setup 完成!"
echo "================================================================"
echo ""
echo "📋 下一步 (用户手动操作):"
echo ""
echo "  1. 把 deploy 公钥写入 /home/deployer/.ssh/authorized_keys"
echo "     (本机 ssh-keygen -t ed25519 -f ~/.ssh/xgzh_deploy 生成)"
echo ""
echo "  2. 拷贝 compose / env 模板到 /opt/xgzh"
echo "     sudo -u deployer cp .../docker-compose.production.yml /opt/xgzh/"
echo "     sudo -u deployer cp .../.env.production.example /opt/xgzh/.env.production.example"
echo ""
echo "  3. 配置 .env"
echo "     sudo -u deployer cp /opt/xgzh/.env.production.example /opt/xgzh/.env"
echo "     sudo -u deployer vi /opt/xgzh/.env  # 填 PG 密码 + JWT_SECRET + LLM key"
echo ""
echo "  4. GitHub repo Settings → Secrets 配置 8 个 secret (见 spec/25 § Step 4)"
echo ""
echo "  5. 首次启动 PG/Redis"
echo "     sudo -u deployer bash -c 'cd /opt/xgzh && docker compose -f docker-compose.production.yml up -d postgres redis'"
echo ""
echo "  6. 本机 git push 触发首次部署 (CI 跑通后自动部署)"
echo ""
echo "📌 快速验证:"
echo "     systemctl status docker fail2ban"
echo "     ufw status verbose"
echo "     free -h  # 应看到 swap 1G"
echo "     id deployer  # 应在 docker 组"
echo ""
