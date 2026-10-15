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
| `deployer` | Kéo repo pipeline, kiểm manifest, cài pipeline đã merge vào Dagster (cấu hình ở mục 6) | không |
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

VM Azure B2s có 4 GB RAM: giữ backend ở **một tiến trình uvicorn** và đặt hai biến trong môi trường backend
(Compose đọc từ `~/atomesus/.env`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PIPELINE_MAX_PARALLEL_SANDBOX` | `1` | Số sandbox dlt + dbt + DuckDB chạy đồng thời, dùng chung cho run thường, batch, optimizer, tối ưu thủ công và regression. |
| `PIPELINE_MAX_PARALLEL_PLANNING` | `3` | Số luồng lập kế hoạch đồng thời trong mỗi batch, tránh mở tới 20 luồng trên VM 4 GB. |

Cả hai phải là số nguyên từ 1. Run vượt số chỗ sandbox chờ theo FIFO, timeline ghi
`Đang xếp hàng chạy thử (vị trí k)`; đổi vị trí cập nhật mốc cũ, đọc qua `GET /api/v1/runs/{id}`.
Thời gian chờ không tính vào `phase_timeout_sec` và vẫn hủy được; kiểm tra hủy tối đa mỗi giây.
Phase timeout trả chỗ ngay và ghi cảnh báo kèm run_id; luồng Python bị treo có thể còn sống tới khi công việc thoát.
Không tăng số sandbox trên VM 4 GB khi chưa đo RAM thực tế. Bộ giới hạn dùng chung trong một tiến trình;
nhiều worker uvicorn sẽ có các bộ giới hạn riêng.

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

### Kết nối Postgres của người dùng

Đặt `PIPELINE_EGRESS_IP=<IP public của VM>` trong `~/atomesus/.env` rồi tạo lại container web (`docker compose ... up -d web`) để trang Nguồn dữ liệu hiện IP cần mở firewall; không cần build lại image.

Trước khi cập nhật backend, tạo một khóa Fernet và đặt `PIPELINE_CONNECTION_KEY` trong `~/atomesus/.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Backend đọc biến này qua `env_file` của Compose. Ở `APP_ENV=staging` hoặc `production`, thiếu khóa hoặc khóa sai
định dạng sẽ làm backend từ chối khởi động với thông báo nêu rõ `PIPELINE_CONNECTION_KEY`. Development/test cho phép
khởi động không có khóa và kiểm tra kết nối, nhưng vẫn từ chối lưu kết nối cho đến khi có khóa. Dependency
`cryptography` nằm trong `requirements.txt`.

Giữ nguyên khóa qua các lần cập nhật; sao lưu khóa riêng, an toàn cùng bản sao SQLite tại `PIPELINE_STORE_PATH`.
Không tự thay khóa khi đã có kết nối: mật khẩu cũ không giải mã được bằng khóa mới. Không đưa khóa vào Git hoặc log.

API dưới `/api/v1/connections` dùng cookie phiên đăng nhập:

| API | Hành vi |
|---|---|
| `POST /test` | Engineer kiểm tra kết nối, không lưu; lỗi trả 422 với lý do tiếng Việt |
| `POST /` | Engineer lưu sau khi kiểm tra thành công; trả 201, mật khẩu được mã hóa Fernet |
| `GET /` | Chỉ liệt kê kết nối thuộc người gọi; không trả mật khẩu hoặc ciphertext. Reviewer chỉ nhận id và tên |
| `DELETE /{id}` | Engineer xóa kết nối của mình; trả 204, kết nối người khác trả 404, pipeline đã deploy đang dùng trả 409 |

Body của hai API POST gồm `name` (mặc định `Postgres`), `host`, `port` (mặc định 5432), `database`, `user`,
`password`, `sslmode` (mặc định `require`; cho phép `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`).
Chủ sở hữu luôn lấy từ session, không nhận từ body. Reviewer không được POST hoặc DELETE (403).

User nguồn cần quyền `USAGE` trên schema và `SELECT` trên ít nhất một bảng/view dữ liệu; database chưa có bảng đọc
được cũng trả 422. Kết nối thử dùng `connect_timeout=5`, `default_transaction_read_only=on`, `statement_timeout=5000`
và `lock_timeout=2000`. Nên dùng `verify-full` với chứng chỉ CA tin cậy và tài khoản nguồn chỉ đọc.

Backend kiểm mọi địa chỉ DNS rồi kết nối bằng IP đã kiểm tra, giữ tên host cho xác minh SSL. Host loopback, private,
link-local, reserved, multicast và service nội bộ (`postgres`, `backend`, `dagster`, `deployer`, `mock-api`, `web`,
`caddy`) bị chặn. `PIPELINE_CONNECTION_ALLOW_PRIVATE=1` chỉ có hiệu lực ở `development`/`test`; staging/production
luôn bỏ qua biến này. Sổ kết nối này chưa bao gồm chọn schema/bảng hoặc chạy pipeline bằng `conn:<id>`.

## 6. Bật deployer: đưa pipeline đã duyệt lên Dagster production

**Chế độ Deploy** (`PIPELINE_DEPLOY_MODE`, ADR D6; compose truyền cùng giá trị cho `backend` và `deployer`):

- `direct` (mặc định): Reviewer bấm Deploy → backend giữ mọi kiểm tra (quyền, chữ ký duyệt khớp `files_hash`, giữ tên
  model) rồi ghi bundle bất biến `data/releases/inbox/<run_id>-v<version>/` (đúng 4 + N file đã duyệt và
  `manifest.json`, ghi vào thư mục tạm rồi rename). Run giữ `APPROVED`, `stage_reached=deploy` tới khi deployer báo.
  Mỗi chu kỳ deployer kiểm manifest và sha256 từng file, cài + chạy thử như dưới đây, báo callback (không có
  `commit_sha`) rồi chuyển bundle sang `done/`; bundle sai (bị sửa, thiếu/thừa file) báo `failed` → `DEPLOY_FAILED`
  và chuyển sang `rejected/`. Không cần GitHub token, repo pipeline, deploy key hay `DEPLOYER_REPO_URL`; chỉ cần
  `PIPELINE_DEPLOYER_SECRET` (bước 2). Backend chỉ ghi `inbox/`. Nếu `data/releases` chưa có, tạo trước khi
  `up -d` để thư mục thuộc uid 1000: `mkdir -p ~/atomesus/data/releases` (Docker tự tạo thì thuộc root, deployer
  không chuyển được bundle; sửa bằng `sudo chown -R 1000:1000 ~/atomesus/data/releases`).
- `pr`: luồng repo pipeline bên dưới (bước 1–2 đầy đủ).

Ở chế độ `pr`, pipeline chỉ lên Dagster production qua repo pipeline (SPEC §9.5): Reviewer bấm Deploy → backend mở pull request → CI
kiểm manifest → người có quyền merge → service `deployer` trên VM kéo nhánh mặc định (mặc định mỗi 60 giây), kiểm
manifest lần nữa, cài vào volume `dagster_code` (nguyên tử, giữ bản trước tới khi xác nhận xong). Deployer ghi mốc thời
gian ngay trước khi thay thư mục pipeline, rồi hỏi GraphQL `workspaceOrError`: chỉ khi location có `loadStatus=LOADED`,
`updatedTimestamp` từ mốc cài trở đi và đúng job đã nạp mới kích hoạt đúng **một** lần chạy `<model>_job`. Trong lúc
chờ watcher khởi động lại Dagster, chỉ đọc workspace; không gửi RELOAD/LAUNCH tới tiến trình cũ còn trả lời.
Chờ workspace, kích hoạt và chờ kết quả dùng chung hạn 120 giây (mức D2), không chờ cố định 4 giây. Lỗi kết nối tạm
thời được thử lại trong hạn đó ở cả chờ workspace, RELOAD (khi gọi verifier ngoài luồng cài), LAUNCH và STATUS.
Location mới báo lỗi nạp `PythonError` hoặc launch bị từ chối (`RunConfigValidationInvalid`, `PipelineNotFoundError`,
`PythonError`, …) thì thất bại ngay, detail chỉ ghi tên lỗi, không chứa thông báo thô của Dagster. Nếu LAUNCH có thể đã được
Dagster nhận nhưng mất phản hồi, deployer tìm run theo tag riêng thay vì gửi lại mutation gây chạy trùng.
Chỉ khi lần chạy đó `SUCCESS` mới báo `deployed` kèm run id Dagster trong `detail` và xóa bản sao lưu. `FAILURE`,
`CANCELED`, lỗi nạp location/từ chối launch hoặc hết hạn chờ thì khôi phục đúng thư mục pipeline trước đó;
lần cài đầu thì gỡ code location.
Nếu xóa thư mục mới lỗi, deployer cách ly nó ở tên `.tmp-…`, vẫn khôi phục backup và ghi lại workspace trước khi
báo lỗi kèm nguyên nhân chạy thử. Deployer ghi lại `workspace.yaml` để Dagster nạp bản khôi phục, rồi báo `failed` với lý
do ngắn. Callback (`POST /api/v1/deployments/callback`, ký HMAC) chuyển run sang `DEPLOYED` / `DEPLOY_FAILED`.

Hợp đồng callback giữa deployer và backend dùng hai header: `X-Atomesus-Timestamp` là Unix time theo giây và
`X-Atomesus-Signature` là `sha256=<hex HMAC-SHA256>`. Dữ liệu ký chính xác là
`<timestamp ASCII>.<raw request body>` với khóa `PIPELINE_DEPLOYER_SECRET`. Backend chỉ nhận timestamp lệch tối đa 5 phút
so với đồng hồ của backend; deployer tạo timestamp và chữ ký mới khi báo lại ở chu kỳ sau. Vì vậy hai máy/container phải
đồng bộ giờ, và thay timestamp mà không ký lại sẽ bị từ chối `401`.

CI đỏ đến sau khi PR đã merge chỉ ghi kết quả CI, không đưa run về P4: code đã ở nhánh mặc định, trạng thái production
do callback deployer quyết định. Rollback khôi phục code; dữ liệu mà lần chạy thử đã ghi vào warehouse không được
hoàn tác. Khi quá hạn hoặc mất phản hồi sau launch, deployer yêu cầu hủy run (tối đa thêm 3 giây cho yêu cầu dọn dẹp);
nếu Dagster mất kết nối thì cần kiểm tra run còn chạy trên UI khi dịch vụ hoạt động lại.

Chỉ `deployer` ghi được `dagster_code`; `dagster` và `backend` mount chỉ đọc, không service nào khác ghi volume này.
`deployer` không đọc `.env` nên không có OpenAI key hay mật khẩu nguồn; nó chỉ có deploy key **chỉ đọc** của repo
pipeline. Service luôn chạy; chưa cấu hình thì nó ghi log `Deployer chưa bật: …` mỗi giờ và nằm chờ, Dagster chạy với
danh sách pipeline rỗng. Pipeline đã cài vào `~/atomesus/data/dagster_local` trước khi có deployer không còn được Dagster
đọc: Deploy lại chúng từ app.

Image của `deployer` là image app (build từ `Dockerfile` cho `backend`) thêm `git` và `openssh-client`, build trong
`deploy/compose.yml` (`additional_contexts: service:backend`, cần Docker Compose v2 bản mới như `get.docker.com` cài).
Image này mang sẵn `workspace.yaml` rỗng và thư mục trạng thái thuộc user `app` (uid 1000): volume `dagster_code` /
`deployer_state` lần đầu mount vào deployer được Docker chép nội dung và chủ sở hữu từ image, nên không cần bước
`chown` nào bằng root. Dagster ghi kết quả dbt từng lần chạy vào `data/dagster_home/dbt_target` (không ghi vào code
location); Day 2 gom `run_results.json` theo pipeline ở `data/dagster_home/day2_dbt` để đếm test.
Khóa host Ed25519 của `github.com` được ghim sẵn trong `src/deployer/github_known_hosts`, theo
[khóa SSH GitHub công bố](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
Deployer luôn dùng `StrictHostKeyChecking=yes`, không tự tin host mới hoặc lấy khóa qua `ssh-keyscan`.

Cần có trước: repo pipeline và branch protection (xem `.harness/plans/DEPLOY-PR.md`).

**1. Tạo deploy key chỉ đọc** (trên VM; khóa nằm ngoài `~/atomesus` vì workflow Deploy rsync `--delete` thư mục đó, và
ngoài `data/` vì backend đọc được):

```bash
sudo mkdir -p /etc/atomesus-deployer
sudo ssh-keygen -t ed25519 -N '' -C atomesus-deployer -f /etc/atomesus-deployer/deploy_key
sudo chown -R 1000:1000 /etc/atomesus-deployer    # uid của user `app` trong container
sudo chmod 600 /etc/atomesus-deployer/deploy_key
sudo cat /etc/atomesus-deployer/deploy_key.pub
```

Repo pipeline → **Settings → Deploy keys → Add deploy key**: dán nội dung `deploy_key.pub`, **không** tích
"Allow write access". Không dùng khóa này cho việc khác.

**2. Biến môi trường** — thêm vào `~/atomesus/.env`:

| Biến | Giá trị |
|---|---|
| `PIPELINE_DEPLOY_MODE` | tùy chọn, `direct` (mặc định, cài từ hộp thư release) hoặc `pr` (cài từ repo pipeline sau merge) |
| `DEPLOYER_REPO_URL` | chỉ chế độ `pr`: URL SSH repo pipeline, vd. `git@github.com:<owner>/atomesus-pipelines.git` |
| `PIPELINE_DEPLOYER_SECRET` | `openssl rand -hex 32`; backend đọc cùng biến này từ `.env` để kiểm chữ ký callback |
| `DEPLOYER_INTERVAL_SEC` | tùy chọn, chu kỳ kéo repo (mặc định `60`, tối thiểu `5`) |
| `DEPLOYER_DAGSTER_URL` | tùy chọn, URL GraphQL nội bộ của Dagster, mặc định `http://dagster:3070` (deployer thêm `/graphql`) |
| `DEPLOYER_VERIFY_TIMEOUT_SEC` | tùy chọn, tổng hạn chờ nạp workspace, kích hoạt và xác nhận lần chạy thử; mặc định `120`, số giây nguyên từ `1` |
| `DEPLOYER_SSH_DIR` | tùy chọn, thư mục chứa `deploy_key` (mặc định `/etc/atomesus-deployer`) |
| `DEPLOYER_KNOWN_HOSTS` | tùy chọn cho Git host khác: đường dẫn **trong container**, vd. `/run/secrets/deployer/known_hosts`; đặt file khóa host đã xác minh vào `DEPLOYER_SSH_DIR`. Mặc định dùng khóa GitHub ghim trong image. |

Compose đặt `DEPLOYER_SSH_KEY=/run/secrets/deployer/deploy_key`, `DEPLOYER_STATE_DIR=/app/state`,
`PIPELINE_DEPLOY_DIR=/app/data/dagster_local`, `PIPELINE_RELEASE_DIR=/app/data/releases` (cả backend) và
`DEPLOYER_BACKEND_URL=http://backend:8000`. Khi chạy script ngoài
Compose có thể cấu hình các biến này; khóa SSH luôn đọc từ file, không truyền nội dung khóa trong dòng lệnh.

Deployer không ghi URL repo, khóa hay secret ra log.
Khi `dbt parse` lỗi hoặc quá 120 giây, log deployer và `detail` callback có đuôi của cả stdout/stderr, tối đa
140 ký tự mỗi luồng sau khi che giá trị secret/URL trong môi trường, URL trong output và trường mật khẩu/token/khóa.
Toàn bộ detail callback giới hạn 500 ký tự. Dùng phần lỗi biên dịch/macro này để xác định file dbt cần sửa.

**3. Chạy và kiểm tra:**

```bash
cd atomesus
docker compose --env-file .env -f deploy/compose.yml up -d --build deployer   # đổi .env thì tạo lại container
docker compose --env-file .env -f deploy/compose.yml exec deployer sh -c 'git --version && ssh -V'
# Thử deploy key: GitHub trả "Hi <owner>/<repo>! You've successfully authenticated" (mã thoát 1 là bình thường)
docker compose --env-file .env -f deploy/compose.yml exec deployer ssh -T -i /run/secrets/deployer/deploy_key \
  -F /dev/null -o UserKnownHostsFile=/app/src/deployer/github_known_hosts \
  -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes git@github.com
docker compose --env-file .env -f deploy/compose.yml logs -f deployer
docker compose --env-file .env -f deploy/compose.yml exec dagster cat /app/data/dagster_local/workspace.yaml
docker compose --env-file .env -f deploy/compose.yml exec deployer cat /app/state/state.json
```

Log bình thường sau khi chạy thử xanh: `Đã cài fct_x v2 …`, `Commit <sha>: kiểm/cài ['fct_x'] …`; Dagster in `workspace.yaml đổi … khởi
động lại` và pipeline hiện trên `https://dagster.<DOMAIN>`. Không có commit mới thì deployer im lặng. Muốn kéo ngay thì
`restart deployer`.

`state.json` giữ `applied` (commit mà mọi pipeline đổi đã cài **và** backend đã nhận báo `deployed`) và `models` (kết
quả từng pipeline của commit mới nhất chưa xong). Chỉ khi mọi pipeline xong thì `applied` mới tiến; pipeline chưa xong
không bị bỏ qua ở chu kỳ sau. Một pipeline lỗi không chặn pipeline khác: pipeline tốt vẫn được cài và báo (một lần).

**Xử lý khi cài hoặc chạy thử lỗi:** xem `logs -f deployer` để đọc lý do `Chạy thử <model> lỗi: …`, và `error_trace`
(stage `deploy`) của RunReport trên app/API để xem lý do callback đã lưu. `state.json` ghi kết quả `failed` theo sha
cây của pipeline: không cài/chạy thử lại cùng nội dung ở mỗi chu kỳ, kể cả commit mới chỉ đổi pipeline khác. Commit
mới đụng pipeline đó (nội dung mới đã duyệt) mới được thử lại; `--reinstall` là thao tác ép thử lại của người vận hành.

| Log / trạng thái trong `models` | Nghĩa và cách xử lý |
|---|---|
| `Chạy thử <model> lỗi: … Đã quay về bản trước` → `failed` | Job `FAILURE` / `CANCELED` hoặc hết hạn chờ (kể cả Dagster vẫn không trả lời / lỗi GraphQL). Bản cũ đã khôi phục, lần cài đầu đã gỡ location; run chuyển `DEPLOY_FAILED`. Nếu kèm lỗi cách ly/khôi phục/ghi workspace, kiểm tra filesystem và backup trước khi cài lại. Kiểm tra lần chạy trên Dagster UI và lỗi trong RunReport, sửa pipeline rồi duyệt/merge nội dung mới. |
| `Không cài <model> … sha256 không khớp` / `run_report.json không khớp` / `Chỉ cài file thường` → `failed` | Nội dung trên nhánh mặc định khác chữ ký duyệt (bị sửa tay sau khi duyệt). Run chuyển `DEPLOY_FAILED`. Không sửa tay repo pipeline: Deploy lại từ app để mở pull request mới; version mới merge thì deployer tự cài. |
| `… không báo được backend` → `unreported` | `manifest.json` hỏng tới mức thiếu run_id/version/files_hash; không báo được run nào. Xử lý như dòng trên. |
| `Cài <model> lỗi, giữ bản đang chạy: dbt parse …` / `Sẽ cài lại … ở chu kỳ sau` → `retry` | Lỗi môi trường khi biên dịch (đĩa, dbt). Deployer tự cài lại ở 2 chu kỳ sau; vẫn lỗi thì báo `failed` (trạng thái `failed`). Sửa môi trường xong thì cài lại: `stop deployer`, `run --rm deployer python scripts/deployer.py --once --reinstall`, `start deployer`. |
| `Báo backend kết quả … lỗi` / `chưa cài commit mới` → `reporting` | Backend không nhận callback (đang tắt, `PIPELINE_DEPLOYER_SECRET` khác nhau → 401). Deployer giữ kết quả, báo lại mỗi chu kỳ và chưa cài commit mới cho tới khi báo được. |
| `Backend từ chối kết quả …` → `refused` | Backend trả 400/409/422 (run không còn `APPROVED` hoặc không khớp chữ ký). Pipeline đã cài nhưng không gửi lại cùng kết quả; kiểm run trên app, xong thì `--reinstall` như trên hoặc Deploy version mới. |
| `Deployer chưa bật: Thiếu DEPLOYER_REPO_URL …` | Thiếu biến ở bước 2; sửa `.env` rồi `up -d deployer`. |
| `git fetch lỗi … Permission denied (publickey)` | Deploy key chưa thêm vào repo pipeline, sai `DEPLOYER_REPO_URL`, hoặc file khóa không thuộc uid 1000 / không phải quyền 600. |
| `Host key verification failed` | Host khác GitHub cần file khóa host đã xác minh và `DEPLOYER_KNOWN_HOSTS`. Nếu GitHub đổi khóa, xác minh trên tài liệu chính thức rồi cập nhật khóa ghim và build lại image; không tắt kiểm host. |
| `Không đọc được file khóa SSH` | Chưa có `deploy_key` trong `DEPLOYER_SSH_DIR` (bước 1), hoặc đặt sai thư mục. |
| `Không chạy được git` | Image deployer cũ chưa có `git` / `openssh-client`: `up -d --build deployer`. |
| `state.json hỏng` | `run --rm deployer rm /app/state/state.json`; chu kỳ sau cài lại mọi pipeline ở commit mới nhất. |

(Mọi lệnh trong bảng là `docker compose --env-file .env -f deploy/compose.yml <lệnh>`.)
