#!/usr/bin/env bash
# Khởi động Atomesus trong một Hugging Face Space (docs/deploy-free.md): mock API, backend, Dagster, sao lưu data/
# và Caddy (cổng 7860). Một tiến trình chết thì cả container dừng để Space tự khởi động lại.
set -euo pipefail
cd /app

for key in OPENAI_API_KEY PIPELINE_DATABASE_URL PIPELINE_ENGINEER_PASSWORD PIPELINE_REVIEWER_PASSWORD DAGSTER_PASSWORD; do
  if [ -z "${!key:-}" ]; then
    echo "Thiếu secret $key (Space → Settings → Variables and secrets), xem docs/deploy-free.md." >&2
    exit 1
  fi
done

# Khôi phục lỗi thì dừng hẳn: chạy tiếp với data/ rỗng sẽ ghi đè bản sao lưu.
python scripts/space_sync.py restore

export PIPELINE_MOCK_API_URL=http://127.0.0.1:8100
export PIPELINE_DAGSTER_URL=http://127.0.0.1:3070/dagster
export DAGSTER_WEBSERVER_PATH_PREFIX=/dagster
export DAGSTER_USER=${DAGSTER_USER:-admin}
DAGSTER_PASSWORD_HASH=$(caddy hash-password --plaintext "$DAGSTER_PASSWORD")
export DAGSTER_PASSWORD_HASH

uvicorn infra.mock_api.app:app --host 127.0.0.1 --port 8100 &
uvicorn src.main:app --host 127.0.0.1 --port 8000 &
DAGSTER_HOST=127.0.0.1 DAGSTER_PORT=3070 python scripts/dagster_local.py &
python scripts/space_sync.py loop &
caddy run --config deploy/hf-space/Caddyfile --adapter caddyfile &

# Space dừng (deploy bản mới, ngủ): chuyển SIGTERM cho các tiến trình con để space_sync kịp sao lưu lần cuối.
trap 'kill -TERM $(jobs -p) 2>/dev/null; wait' TERM INT
wait -n || true
echo "Một tiến trình đã dừng, tắt container để Space khởi động lại." >&2
kill -TERM $(jobs -p) 2>/dev/null
wait
exit 1
