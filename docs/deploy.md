# Deploy Atomesus lên Oracle Cloud (miễn phí) và tự cập nhật khi merge vào `main`

Toàn bộ sản phẩm chạy trên **một VM Ampere A1 (ARM) của Oracle Cloud Always Free** bằng `deploy/compose.yml`:

| Dịch vụ | Vai trò | Ra Internet |
|---|---|---|
| `caddy` | HTTPS tự động, chuyển request vào web / Dagster | 80, 443 |
| `web` | Next.js | qua Caddy: `https://<DOMAIN>` |
| `backend` | FastAPI + agent (chạy thử dlt / dbt trong sandbox) | không (web gọi qua mạng nội bộ) |
| `dagster` | Chạy lịch các pipeline đã duyệt, thu số đo Day 2 | qua Caddy, có mật khẩu: `https://dagster.<DOMAIN>` |
| `mock-api` | Nguồn REST API demo | không |
| `postgres` | Nguồn Postgres demo (sales_raw, Pagila) | không |

**Cập nhật tự động:** merge vào `main` → workflow "Postgres pipeline" (lint + test với Postgres thật) chạy xanh →
workflow **Deploy** đẩy code lên VM và `docker compose up -d --build`. Khoảng 3–10 phút sau sản phẩm chạy bản mới.
Test đỏ thì không deploy. Bấm **Actions → Deploy → Run workflow** để deploy tay.

**Không mất khi cập nhật:** `~/atomesus/data` (sổ bằng chứng, chat, warehouse DuckDB, project Dagster đã duyệt, lịch sử
Dagster), volume `pgdata` (dữ liệu nguồn), `~/atomesus/.env` (mật khẩu, khóa API). Pipeline đã duyệt không tự đổi theo code
mới; muốn dùng logic mới thì tạo version mới và duyệt lại.

---

## 1. Tạo VM (làm một lần, trên web Oracle)

1. Đăng ký <https://www.oracle.com/cloud/free/> (cần thẻ để xác minh, không bị trừ tiền với tài nguyên Always Free).
   Chọn **Home Region** gần (ví dụ Singapore, Tokyo); không đổi được về sau.
2. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 24.04**
   - Shape: **Ampere → VM.Standard.A1.Flex**, **4 OCPU, 24 GB RAM** (mức tối đa miễn phí)
   - Networking: để mặc định (tạo VCN mới, subnet public, **gán public IPv4**)
   - SSH keys: tải khóa Oracle sinh ra, hoặc dán khóa công khai của bạn
   - Báo "Out of capacity" thì thử lại sau hoặc chọn Availability Domain khác.
3. **Mở cổng 80/443:** Networking → Virtual Cloud Networks → VCN vừa tạo → Security Lists → Default →
   **Add Ingress Rules**: Source `0.0.0.0/0`, TCP, Destination port `80`; thêm một dòng nữa cho `443`.
4. Ghi lại **Public IP** của VM.

## 2. Cài VM (một lần, ~5 phút)

Trên máy bạn (thư mục repo):

```bash
scp -i <khoa-ssh> deploy/setup-vm.sh ubuntu@<IP>:~
ssh -i <khoa-ssh> ubuntu@<IP> 'bash setup-vm.sh'
```

Script cài Docker, mở cổng trong iptables của VM (ảnh Ubuntu của Oracle chặn sẵn), hỏi **tên miền** (Enter để dùng
`atomesus.<ip>.sslip.io`, miễn phí, không cần mua) và **OpenAI API key**, rồi tạo `~/atomesus/.env` với mật khẩu ngẫu nhiên.
**Lưu lại** mật khẩu Engineer / Reviewer / Dagster script in ra.

Dùng tên miền riêng: tạo bản ghi A `app.ten-mien.vn` và `dagster.app.ten-mien.vn` trỏ về IP, nhập `app.ten-mien.vn`.

## 3. Cho GitHub quyền deploy (một lần)

Tạo một khóa SSH **chỉ dùng để deploy** (không dùng khóa cá nhân):

```bash
ssh-keygen -t ed25519 -N "" -C atomesus-deploy -f atomesus_deploy
ssh-copy-id -i atomesus_deploy.pub -o "IdentityFile <khoa-ssh>" ubuntu@<IP>
```

Repo GitHub → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Giá trị |
|---|---|
| `DEPLOY_HOST` | Public IP của VM |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | toàn bộ nội dung file `atomesus_deploy` (khóa riêng) |

Rồi **Actions → Deploy → Run workflow** (nhánh `main`). Lần đầu build mất 10–20 phút trên VM ARM; các lần sau chỉ build lại
phần code đổi. Xong thì mở `https://<DOMAIN>`.

## 4. Giới hạn tiền OpenAI

Hai lớp, nên bật cả hai:

1. **Trong app (đã có):** `policy/budgets.yml`
   - `per_day.max_usd` (mặc định 2 USD/ngày, tính cả quét dữ liệu và LLM): chạm trần thì mọi run dừng ở `BLOCKED_BUDGET`
     **trước** khi gọi LLM, tới ngày hôm sau (UTC).
   - `per_run.max_llm_calls` (6 lần/run).
   - Đổi trần: sửa file, merge vào `main`, tự deploy.
2. **Trên tài khoản OpenAI** (chặn cả khi app có lỗi, hoặc khóa bị lộ):
   - <https://platform.openai.com/settings/organization/billing>: dùng **trả trước (credit)** và **tắt Auto recharge**.
     Hết credit thì API từ chối mọi request, đây là trần cứng.
   - Tạo **Project riêng** cho bản deploy (Settings → Projects), đặt **Budget** hằng tháng và email cảnh báo, rồi tạo
     API key trong project đó cho VM. Không dùng chung khóa với máy cá nhân.
   - Settings → Limits của project: chỉ cho phép các model app dùng (`gpt-4o-mini`; thêm `gpt-4o` nếu đặt
     `MODEL_NAME_STRONG`).
   - Lộ khóa: thu hồi trên trang API keys, sửa `OPENAI_API_KEY` trong `~/atomesus/.env` trên VM rồi chạy lại workflow Deploy.

## 5. Vận hành

```bash
ssh ubuntu@<IP>
cd atomesus
docker compose --env-file .env -f deploy/compose.yml ps            # trạng thái
docker compose --env-file .env -f deploy/compose.yml logs -f backend  # log (backend, web, dagster, caddy…)
docker compose --env-file .env -f deploy/compose.yml restart dagster
```

- Đổi mật khẩu / khóa: sửa `~/atomesus/.env`, rồi `docker compose ... up -d`.
- Sao lưu: `tar czf atomesus-data.tgz data` và `docker run --rm -v atomesus_pgdata:/v -v $PWD:/b busybox tar czf /b/pgdata.tgz -C /v .`
- Đưa dữ liệu demo từ máy local lên (run mẫu, pipeline đã duyệt): dừng stack, `scp -r data ubuntu@<IP>:atomesus/`,
  `sudo chown -R 1000:1000 ~/atomesus/data`, chạy lại `up -d`.

**Bảo mật của bản demo:** đăng nhập vẫn là hai tài khoản local (`APP_ENV=staging`, backend từ chối mật khẩu mặc định).
Chưa có SSO, nên chỉ đưa mật khẩu cho người cần dùng; ai đăng nhập được Engineer là tiêu được tiền LLM (trong trần ở mục 4).
