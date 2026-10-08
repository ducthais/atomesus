#!/usr/bin/env bash
# Chuẩn bị VM Ubuntu (Oracle Cloud Always Free) cho Atomesus, chạy MỘT lần bằng user ubuntu:
#   bash setup-vm.sh
# Việc làm: cài Docker, mở cổng 80/443 trong iptables của VM, tạo ~/atomesus/.env với mật khẩu ngẫu nhiên.
# Code do GitHub Actions đẩy lên (.github/workflows/deploy.yml); script không cần quyền đọc repo.
# Chạy lại an toàn: không ghi đè .env đã có.
set -euo pipefail

APP_DIR="$HOME/atomesus"

echo "== Docker =="
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
sudo apt-get install -y rsync >/dev/null

echo "== Mở cổng 80/443 =="
# Ảnh Ubuntu của Oracle chặn mọi cổng trừ 22 bằng iptables (ngoài Security List của VCN, phải mở cả hai).
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 6 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
if command -v netfilter-persistent >/dev/null; then
  sudo netfilter-persistent save
fi

mkdir -p "$APP_DIR/data"
sudo chown -R 1000:1000 "$APP_DIR/data"  # uid của user `app` trong image (Dockerfile)

if [ -f "$APP_DIR/.env" ]; then
  echo "== $APP_DIR/.env đã có, giữ nguyên =="
  exit 0
fi

echo "== Tạo $APP_DIR/.env =="
secret() { openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c "${1:-24}"; }
ip=$(curl -fsS https://api.ipify.org)
default_domain="atomesus.${ip//./-}.sslip.io"
read -r -p "Tên miền cho web [$default_domain]: " domain
domain=${domain:-$default_domain}
read -r -s -p "OpenAI API key (sk-...): " openai_key
echo
reader=$(secret)
engineer=$(secret 16)
reviewer=$(secret 16)
dagster_password=$(secret 16)
# Caddy cần bcrypt; mã hóa base64 để không có ký tự `$` trong .env.
dagster_hash=$(sudo docker run --rm caddy:2 caddy hash-password --plaintext "$dagster_password" | base64 -w0)

umask 077
cat >"$APP_DIR/.env" <<EOF
# Sinh bởi deploy/setup-vm.sh lúc $(date -u +%FT%TZ). KHÔNG commit.
APP_ENV=staging
OPENAI_API_KEY=$openai_key
DOMAIN=$domain
DAGSTER_DOMAIN=dagster.$domain
DAGSTER_USER=admin
DAGSTER_PASSWORD_HASH=$dagster_hash
POSTGRES_PASSWORD=$(secret)
PIPELINE_READER_PASSWORD=$reader
PIPELINE_DATABASE_URL=postgresql://pipeline_reader:$reader@postgres:5432/pipeline_demo
PIPELINE_MOCK_API_URL=http://mock-api:8100
PIPELINE_DAGSTER_URL=http://dagster:3070
PIPELINE_ENGINEER_PASSWORD=$engineer
PIPELINE_REVIEWER_PASSWORD=$reviewer
EOF

cat <<EOF

Xong. Lưu lại các thông tin dưới đây (chỉ hiện một lần, nằm trong $APP_DIR/.env):
  Web:      https://$domain
  Engineer: engineer / $engineer
  Reviewer: reviewer / $reviewer
  Dagster:  https://dagster.$domain   (admin / $dagster_password)

Đăng xuất SSH rồi vào lại để nhóm docker có hiệu lực, sau đó chạy workflow Deploy trên GitHub.
EOF
