# Deploy Atomesus lên một VM và tự cập nhật khi merge vào `main`

Toàn bộ sản phẩm chạy trên **một VM Ubuntu có Docker** bằng `deploy/compose.yml`, cần khoảng 4 GB RAM trở lên.
Bước 1 có hai lựa chọn: **Azure for Students** (1a, dùng credit sinh viên) hoặc **Oracle Cloud Always Free** (1b, miễn
phí lâu dài nhưng đăng ký lâu). Từ bước 2 trở đi giống nhau.

Không cần thẻ: chọn 1a (Azure for Students). Combo Vercel + Hugging Face Space + Supabase ở
[deploy-free.md](deploy-free.md) cần gói Hugging Face PRO.

| Dịch vụ | Vai trò | Ra Internet |
|---|---|---|
| `caddy` | HTTPS tự động, chuyển request vào web / Dagster | 80, 443 |
| `web` | Next.js | qua Caddy: `https://<DOMAIN>` |
| `backend` | FastAPI + agent (chạy thử dlt / dbt trong sandbox) | không (web gọi qua mạng nội bộ) |
| `dagster` | Chạy lịch các pipeline đã duyệt, thu số đo Day 2 | qua Caddy, có mật khẩu: `https://dagster.<DOMAIN>` |
| `deployer` | Kéo repo pipeline, kiểm manifest, cài pipeline đã merge vào Dagster (tùy chọn, mục 6) | không |
| `mock-api` | Nguồn REST API demo | không |
| `postgres` | Nguồn Postgres demo (sales_raw, Pagila) | không |

**Cập nhật tự động:** merge vào `main` → workflow "Postgres pipeline" (lint + test với Postgres thật) chạy xanh →
workflow **Deploy** đẩy code lên VM và `docker compose up -d --build`. Khoảng 3–10 phút sau sản phẩm chạy bản mới.
Test đỏ thì không deploy. Bấm **Actions → Deploy → Run workflow** để deploy tay.
GitHub Actions không chạy được (vd. organization hết hạn mức / lỗi thanh toán): deploy từ máy Windows bằng
`powershell -ExecutionPolicy Bypass -File deploy/push.ps1 -VmHost <IP>` (đưa bản `main` trên GitHub lên VM rồi build).

**Không mất khi cập nhật:** `~/atomesus/data` (sổ bằng chứng, chat, warehouse DuckDB, lịch sử Dagster), volume `pgdata`
(dữ liệu nguồn), volume `dagster_code` (pipeline đang chạy trên Dagster) và `deployer_state`, `~/atomesus/.env` (mật khẩu,
khóa API). Pipeline đã duyệt không tự đổi theo code mới; muốn dùng logic mới thì tạo version mới và duyệt lại.

---

## 1a. Tạo VM trên Azure for Students

1. Đăng ký <https://azure.microsoft.com/free/students> bằng **email trường** và xác minh sinh viên. Thường không cần thẻ;
   được 100 USD credit. Hết credit thì máy dừng, không tự trừ tiền.
2. Portal → **Virtual machines → Create → Azure virtual machine**:
   - Resource group: tạo mới `atomesus`. Virtual machine name: `atomesus`.
   - Region: vùng gần (**Southeast Asia** / **East Asia**). Tài khoản sinh viên chỉ được dùng một số vùng: báo
     "disallowed by policy" thì đổi vùng khác.
   - Image: **Ubuntu Server 24.04 LTS – x64 Gen2**.
   - Size: **Standard_B2s** (2 vCPU, 4 GB, khoảng 30 USD/tháng, đủ khoảng 3 tháng credit) hoặc **Standard_B2ms**
     (8 GB, nhanh hơn, tốn gấp đôi).
   - Authentication type: **SSH public key**, Username **`azureuser`**, Key pair name `atomesus` (Azure tạo khóa mới).
   - Public inbound ports: **Allow selected ports**, chọn **SSH (22), HTTP (80), HTTPS (443)**.
   - Tab Disks: OS disk size **64 GiB**, Standard SSD.
   - **Review + create → Create → Download private key and create resource**. Lưu file `.pem` vào
     `C:\Users\<bạn>\.ssh\atomesus.pem`.
3. Mở VM vừa tạo, chép **Public IP address**. Nên bấm vào IP → **Configuration → Assignment: Static** để IP không đổi.
4. Ở các bước sau: `<user>` là **`azureuser`**, `<khoa-ssh>` là file `.pem` vừa tải.

## 1b. Tạo VM trên Oracle Cloud Always Free (làm một lần, trên web Oracle)

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

Trên máy bạn (PowerShell, thư mục repo). `<user>` là `azureuser` (Azure) hoặc `ubuntu` (Oracle):

```powershell
# Windows: khóa SSH phải chỉ chủ máy đọc được, nếu không ssh từ chối dùng
icacls <khoa-ssh> /inheritance:r /grant:r "$($env:USERNAME):R"
scp -i <khoa-ssh> deploy/setup-vm.sh <user>@<IP>:~
ssh -t -i <khoa-ssh> <user>@<IP> "bash setup-vm.sh"
```

Script cài Docker, mở cổng trong iptables của VM (ảnh Ubuntu của Oracle chặn sẵn), hỏi **tên miền** (Enter để dùng
`atomesus.<ip>.sslip.io`, miễn phí, không cần mua) và **OpenAI API key**, rồi tạo `~/atomesus/.env` với mật khẩu ngẫu nhiên.
**Lưu lại** mật khẩu Engineer / Reviewer / Dagster script in ra.

Dùng tên miền riêng: tạo bản ghi A `app.ten-mien.vn` và `dagster.app.ten-mien.vn` trỏ về IP, nhập `app.ten-mien.vn`.

## 3. Cho GitHub quyền deploy (một lần)

Tạo một khóa SSH **chỉ dùng để deploy** (không dùng khóa cá nhân):

```powershell
ssh-keygen -t ed25519 -N '""' -C atomesus-deploy -f atomesus_deploy
type atomesus_deploy.pub | ssh -i <khoa-ssh> <user>@<IP> "cat >> ~/.ssh/authorized_keys"
```

Repo GitHub → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Giá trị |
|---|---|
| `DEPLOY_HOST` | Public IP của VM |
| `DEPLOY_USER` | `azureuser` (Azure) hoặc `ubuntu` (Oracle) |
| `DEPLOY_SSH_KEY` | toàn bộ nội dung file `atomesus_deploy` (khóa riêng) |

Rồi **Actions → Deploy → Run workflow** (nhánh `main`). Lần đầu build mất 10–25 phút; các lần sau chỉ build lại phần code
đổi. Xong thì mở `https://<DOMAIN>`.

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
ssh -i <khoa-ssh> <user>@<IP>
cd atomesus
docker compose --env-file .env -f deploy/compose.yml ps            # trạng thái
docker compose --env-file .env -f deploy/compose.yml logs -f backend  # log (backend, web, dagster, caddy…)
docker compose --env-file .env -f deploy/compose.yml restart dagster
```

- Đổi mật khẩu / khóa: sửa `~/atomesus/.env`, rồi `docker compose ... up -d`.
- Sao lưu: `tar czf atomesus-data.tgz data` và `docker run --rm -v atomesus_pgdata:/v -v $PWD:/b busybox tar czf /b/pgdata.tgz -C /v .`
- Đưa dữ liệu demo từ máy local lên (run mẫu, pipeline đã duyệt): dừng stack, `scp -r data <user>@<IP>:atomesus/`,
  `sudo chown -R 1000:1000 ~/atomesus/data`, chạy lại `up -d`.

**Bảo mật của bản demo:** đăng nhập vẫn là hai tài khoản local (`APP_ENV=staging`, backend từ chối mật khẩu mặc định).
Chưa có SSO, nên chỉ đưa mật khẩu cho người cần dùng; ai đăng nhập được Engineer là tiêu được tiền LLM (trong trần ở mục 4).

## 6. Bật deployer: đưa pipeline đã merge lên Dagster production

Pipeline chỉ lên Dagster production qua repo pipeline (SPEC §9.5): Reviewer bấm Deploy → backend mở pull request → CI
kiểm manifest → người có quyền merge → service `deployer` trên VM kéo nhánh mặc định (mặc định mỗi 60 giây), kiểm
manifest lần nữa, cài vào volume `dagster_code` (nguyên tử, lỗi thì giữ bản đang chạy), Dagster tự khởi động lại để nạp
pipeline, rồi deployer báo backend (`POST /api/v1/deployments/callback`, ký HMAC) để run chuyển `DEPLOYED` /
`DEPLOY_FAILED`.

Chỉ `deployer` ghi được `dagster_code`; `dagster` và `backend` mount chỉ đọc. `deployer` không đọc `.env` nên không có
OpenAI key hay mật khẩu nguồn; nó chỉ có deploy key **chỉ đọc** của repo pipeline. Chưa bật thì Dagster chạy với danh sách
pipeline rỗng. Pipeline đã cài vào `~/atomesus/data/dagster_local` trước khi có deployer không còn được Dagster đọc:
Deploy lại chúng từ app.

Cần có trước: repo pipeline và branch protection (xem `.harness/plans/DEPLOY-PR.md`), image app có `git` và
`openssh-client`.

**1. Tạo deploy key chỉ đọc** (trên VM; khóa nằm ngoài `~/atomesus` vì workflow Deploy rsync `--delete` thư mục đó, và
ngoài `data/` vì backend đọc được):

```bash
sudo mkdir -p /etc/atomesus-deployer
sudo ssh-keygen -t ed25519 -N '' -C atomesus-deployer -f /etc/atomesus-deployer/deploy_key
ssh-keyscan github.com | sudo tee /etc/atomesus-deployer/known_hosts >/dev/null
ssh-keygen -lf /etc/atomesus-deployer/known_hosts   # so với fingerprint GitHub công bố trong docs "GitHub's SSH key fingerprints"
sudo chown -R 1000:1000 /etc/atomesus-deployer    # uid của user `app` trong container
sudo chmod 600 /etc/atomesus-deployer/deploy_key
sudo cat /etc/atomesus-deployer/deploy_key.pub
```

Repo pipeline → **Settings → Deploy keys → Add deploy key**: dán nội dung `deploy_key.pub`, **không** tích
"Allow write access". Không dùng khóa này cho việc khác.

**2. Biến môi trường** — thêm vào `~/atomesus/.env`:

| Biến | Giá trị |
|---|---|
| `COMPOSE_PROFILES` | `deployer` (bật service; bỏ đi là tắt) |
| `DEPLOYER_REPO_URL` | URL SSH repo pipeline, vd. `git@github.com:<owner>/atomesus-pipelines.git` |
| `PIPELINE_DEPLOYER_SECRET` | `openssl rand -hex 32`; backend đọc cùng biến này từ `.env` để kiểm chữ ký callback |
| `DEPLOYER_INTERVAL_SEC` | tùy chọn, chu kỳ kéo repo (mặc định `60`, tối thiểu `5`) |
| `DEPLOYER_SSH_DIR` | tùy chọn, thư mục chứa `deploy_key` và `known_hosts` (mặc định `/etc/atomesus-deployer`) |

Deployer không ghi URL repo, khóa hay secret ra log.

**3. Chạy và kiểm tra:**

```bash
cd atomesus
docker compose --env-file .env -f deploy/compose.yml up -d
docker compose --env-file .env -f deploy/compose.yml logs -f deployer
docker compose --env-file .env -f deploy/compose.yml exec dagster cat /app/data/dagster_local/workspace.yaml
```

Log bình thường: `Commit <sha>: 1 pipeline đổi ['fct_x']`, `Đã cài fct_x v2 …`; Dagster in `workspace.yaml đổi … khởi
động lại` và pipeline hiện trên `https://dagster.<DOMAIN>`. Không có commit mới thì deployer im lặng. Muốn kéo ngay thì
`restart deployer`.

**Xử lý khi cài lỗi** (bản đang chạy luôn được giữ nguyên):

| Log | Nghĩa và cách xử lý |
|---|---|
| `Không cài <model> … sha256 không khớp` / `run_report.json không khớp` / `Chỉ cài file thường` | Nội dung trên nhánh mặc định khác chữ ký duyệt (bị sửa tay sau khi duyệt). Run chuyển `DEPLOY_FAILED`. Không sửa tay repo pipeline: Deploy lại từ app để mở pull request mới. |
| `Cài <model> lỗi, giữ bản đang chạy: dbt parse …` | Lỗi môi trường khi biên dịch. Sửa xong thì cài lại: `stop deployer`, `run --rm deployer python scripts/deployer.py --once --reinstall`, `start deployer`. |
| `Báo backend kết quả … lỗi` / `chưa cài commit mới` | Backend không nhận callback (đang tắt, `PIPELINE_DEPLOYER_SECRET` khác nhau → 401). Deployer giữ kết quả, báo lại mỗi chu kỳ và chưa cài commit mới cho tới khi báo được. |
| `git fetch lỗi … Permission denied (publickey)` | Deploy key chưa thêm vào repo pipeline, sai `DEPLOYER_REPO_URL`, hoặc file khóa không thuộc uid 1000 / không phải quyền 600. |
| `Host key verification failed` | `known_hosts` thiếu hoặc sai host; tạo lại ở bước 1. |
| `Không chạy được git` | Image app thiếu `git` / `openssh-client`. |
| `state.json hỏng` | `run --rm deployer rm /app/state/state.json`; chu kỳ sau cài lại mọi pipeline ở commit mới nhất. |

(Mọi lệnh trong bảng là `docker compose --env-file .env -f deploy/compose.yml <lệnh>`.)
