#!/usr/bin/env bash
# Chuẩn bị VM Ubuntu (Azure, Oracle Cloud, AWS, DigitalOcean…) cho Atomesus, chạy MỘT lần bằng user đăng nhập của VM:
#   ssh -t <user>@<IP> "bash setup-vm.sh"      (-t để nhập OpenAI key không hiện ra màn hình)
# Việc làm: cài Docker, mở cổng 80/443 nếu iptables của VM chặn (ảnh Oracle), thêm swap nếu RAM < 7 GB,
# tạo ~/atomesus/.env với mật khẩu ngẫu nhiên.
# Code do GitHub Actions đẩy lên (.github/workflows/deploy.yml); script không cần quyền đọc repo.
# Chạy lại an toàn: .env đã có thì không ghi đè giá trị nào, chỉ thêm khóa production còn thiếu
# (PIPELINE_CONNECTION_KEY, PIPELINE_DEPLOYER_SECRET, PIPELINE_ADMIN_PASSWORD) và nhắc đổi APP_ENV=production.
set -euo pipefail

APP_DIR="$HOME/atomesus"

echo "== Docker =="
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
sudo apt-get install -y rsync >/dev/null

echo "== Mở cổng 80/443 =="
# Ảnh Ubuntu của Oracle chặn mọi cổng trừ 22 bằng một luật REJECT trong iptables (ngoài Security List của VCN).
# Azure / AWS / DigitalOcean để iptables trống, cổng mở ở tường lửa của nhà cung cấp (NSG, Security Group): bỏ qua.
if sudo iptables -S INPUT 2>/dev/null | grep -q -- "-j REJECT"; then
  for port in 80 443; do
    if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
      sudo iptables -I INPUT 1 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    fi
  done
  if command -v netfilter-persistent >/dev/null; then
    sudo netfilter-persistent save
  fi
fi

echo "== Swap =="
# Máy 4 GB (Azure B2s, DigitalOcean 4 GB): build Next.js + cài dbt / Dagster cùng lúc có thể hết RAM, thêm 4 GB swap.
if [ "$(free -m | awk '/^Mem:/ {print $2}')" -lt 7000 ] && ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

echo "== Đồng bộ giờ (NTP) =="
# Callback deployer → backend ký kèm timestamp, lệch quá 5 phút bị từ chối (src/api/webhooks.py).
sudo timedatectl set-ntp true 2>/dev/null || echo "Không bật được NTP bằng timedatectl, hãy bật đồng bộ giờ thủ công."
timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qx yes || echo "Lưu ý: giờ hệ thống chưa đồng bộ xong."

mkdir -p "$APP_DIR/data"
sudo chown -R 1000:1000 "$APP_DIR/data"  # uid của user `app` trong image (Dockerfile)

secret() { openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c "${1:-24}"; }
fernet() { openssl rand -base64 32 | tr '+/' '-_'; }  # khóa Fernet: 32 byte, base64 an toàn URL

if [ -f "$APP_DIR/.env" ]; then
  # Không ghi đè giá trị nào; chỉ thêm khóa production còn thiếu (VM dựng trước khi có các khóa này).
  echo "== $APP_DIR/.env đã có: chỉ bổ sung khóa còn thiếu =="
  umask 077
  ensure() {
    if ! grep -q "^$1=" "$APP_DIR/.env"; then
      printf '%s=%s\n' "$1" "$2" >>"$APP_DIR/.env"
      echo "  + $1"
      [ "$1" = PIPELINE_ADMIN_PASSWORD ] && echo "    Admin: admin / $2   (chỉ hiện một lần)"
    fi
    return 0
  }
  ensure PIPELINE_CONNECTION_KEY "$(fernet)"
  ensure PIPELINE_DEPLOYER_SECRET "$(secret 48)"
  ensure PIPELINE_ADMIN_PASSWORD "$(secret 16)"
  if ! grep -qx "APP_ENV=production" "$APP_DIR/.env"; then
    echo "  APP_ENV chưa là production. Khi sẵn sàng: sed -i 's/^APP_ENV=.*/APP_ENV=production/' $APP_DIR/.env"
  fi
  exit 0
fi

echo "== Tạo $APP_DIR/.env =="
ip=$(curl -fsS https://api.ipify.org)
default_domain="atomesus.${ip//./-}.sslip.io"
read -r -p "Tên miền cho web [$default_domain]: " domain
domain=${domain:-$default_domain}
read -r -s -p "OpenAI API key (sk-...): " openai_key
echo
reader=$(secret)
engineer=$(secret 16)
reviewer=$(secret 16)
admin=$(secret 16)
dagster_password=$(secret 16)
# Caddy cần bcrypt; mã hóa base64 để không có ký tự `$` trong .env.
dagster_hash=$(sudo docker run --rm caddy:2 caddy hash-password --plaintext "$dagster_password" | base64 -w0)

umask 077
cat >"$APP_DIR/.env" <<EOF
# Sinh bởi deploy/setup-vm.sh lúc $(date -u +%FT%TZ). KHÔNG commit.
APP_ENV=production
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
PIPELINE_ADMIN_PASSWORD=$admin
# Mã hóa mật khẩu kết nối Postgres người dùng lưu (bắt buộc ở staging/production).
PIPELINE_CONNECTION_KEY=$(fernet)
# Khóa HMAC callback deployer → backend; compose đưa cùng giá trị cho cả hai service.
PIPELINE_DEPLOYER_SECRET=$(secret 48)
EOF

cat <<EOF

Xong. Lưu lại các thông tin dưới đây (chỉ hiện một lần, nằm trong $APP_DIR/.env):
  Web:      https://$domain
  Engineer: engineer / $engineer
  Reviewer: reviewer / $reviewer
  Admin:    admin / $admin
  Dagster:  https://dagster.$domain   (admin / $dagster_password)

Đăng xuất SSH rồi vào lại để nhóm docker có hiệu lực, sau đó chạy workflow Deploy trên GitHub.
EOF
