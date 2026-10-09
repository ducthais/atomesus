# Deploy miễn phí: Vercel + Hugging Face Space + Supabase

Combo lấy từ danh sách dịch vụ chương trình AI20K khuyên dùng (`check.md`). Tất cả **miễn phí và không cần thẻ**.
Khoảng 30 phút cho lần đầu, sau đó merge vào `main` là tự deploy.

| Phần | Chạy ở đâu | Gói miễn phí | URL |
|---|---|---|---|
| Web Next.js | **Vercel** | Hobby | `https://<project>.vercel.app` (URL demo) |
| Backend FastAPI + agent, Dagster, mock API | **Hugging Face Space** (Docker) | CPU Basic: 2 vCPU, 16 GB RAM | `https://<user>-atomesus.hf.space` |
| Postgres nguồn (sales_raw, Pagila) | **Supabase** | 500 MB | không ra Internet ngoài app |
| Sao lưu `data/` | **HF Dataset** riêng tư | miễn phí | không |
| CI/CD | **GitHub Actions** | đã có | không |

Vì sao không dùng Render như bảng stack trong `check.md`: backend cần khoảng 4 GB RAM (dlt, dbt, Dagster chạy cùng
lúc), nhưng Render free chỉ có 512 MB, không có ổ đĩa và tự ngủ sau 15 phút. Space miễn phí có 16 GB RAM.

```
Trình duyệt ──► Vercel (web) ──/api/v1──► Space :7860 (Caddy) ─┬─► FastAPI :8000 ──► Supabase (Postgres nguồn)
                                                                ├─► /dagster (basic auth) ─► Dagster :3070
                                                                └─  mock API :8100, data/ ⇄ HF Dataset mỗi 10 phút
```

## Bước 1: Đăng ký tài khoản (đều không cần thẻ)

- [ ] **Hugging Face**: <https://huggingface.co/join>, rồi **Settings → Access Tokens → Create new token**, loại
  **Write**. Chép token `hf_...`.
- [ ] **Supabase**: <https://supabase.com> → **New project** (Region: Singapore). Nhớ **Database password**.
  Đợi project tạo xong, bấm **Connect** (trên cùng) → mục **Session pooler** → chép **connection string**.
- [ ] **Vercel**: <https://vercel.com/signup> → **Continue with GitHub**.
- [ ] **OpenAI API key**: nên tạo trong một Project riêng có Budget (xem mục 4 của [deploy.md](deploy.md)).

## Bước 2: Chạy script cài đặt (một lần, trên máy bạn)

Cần Docker Desktop đang chạy (để chạy `psql` nạp dữ liệu). Mở PowerShell ở thư mục repo:

```powershell
.venv\Scripts\pip install huggingface_hub
.venv\Scripts\python deploy/hf-space/setup.py
```

Script hỏi token HF, OpenAI key, connection string Supabase (còn `[YOUR-PASSWORD]` thì hỏi thêm mật khẩu), rồi:

1. Nạp `sales_raw` và Pagila vào Supabase, tạo tài khoản chỉ đọc `pipeline_reader`.
2. Tạo Space `<user>/atomesus` (công khai) và dataset riêng tư `<user>/atomesus-data`.
3. Đặt secrets của Space: `OPENAI_API_KEY`, `PIPELINE_DATABASE_URL`, mật khẩu Engineer / Reviewer / Dagster
   (sinh ngẫu nhiên), `HF_TOKEN`.

**Lưu lại** mật khẩu script in ra. Chạy lại script sẽ sinh mật khẩu mới.

## Bước 3: GitHub deploy backend lên Space

Repo GitHub → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Giá trị |
|---|---|
| `HF_TOKEN` | token `hf_...` ở bước 1 |
| `HF_SPACE` | tên Space, ví dụ `ten-ban/atomesus` |

Rồi **Actions → Deploy Space → Run workflow** (nhánh `main`). Lần đầu Space build khoảng 10–15 phút. Workflow
chờ tới khi Space chạy và gọi được `/health` mới báo xanh.

## Bước 4: Vercel deploy web

1. Vercel → **Add New… → Project** → chọn repo này → **Import**.
2. **Root Directory**: bấm Edit, chọn `web`. Framework tự nhận Next.js.
3. **Environment Variables**: `PIPELINE_BACKEND_URL` = URL Space script in ra (`https://<user>-atomesus.hf.space`).
4. **Deploy**. Xong thì mở `https://<project>.vercel.app` và đăng nhập `engineer` / `reviewer`.

Đổi `PIPELINE_BACKEND_URL` thì phải **Redeploy**, vì `web/next.config.ts` đọc biến này lúc build.

## Cập nhật tự động

Merge vào `main` thì:
- **Vercel** tự build web (khoảng 2 phút). Nhánh khác và PR có bản Preview riêng.
- **Space**: workflow "Postgres pipeline" chạy xanh thì **Deploy Space** đẩy code lên, Space build lại (5–15 phút).
  Test đỏ thì không deploy.

## Những điều cần biết

- **Dữ liệu `data/`** (sổ bằng chứng, chat, warehouse, pipeline đã duyệt, lịch sử Dagster): ổ đĩa Space bị xóa mỗi lần
  build lại hoặc khởi động lại, nên `scripts/space_sync.py` sao lưu lên dataset riêng tư **mỗi 10 phút** và khi Space
  dừng, rồi khôi phục khi khởi động. Nếu Space bị tắt đột ngột thì có thể mất tối đa 10 phút dữ liệu gần nhất.
- **Space ngủ** sau 48 giờ không có request. Workflow **Keep Space awake** gọi `/health` mỗi 12 giờ để Dagster
  vẫn chạy lịch. Vào lúc Space đang build lại (khi deploy), web báo không kết nối được backend trong vài phút.
- **Supabase tạm dừng** project sau 7 ngày không có truy vấn. Gặp lỗi kết nối nguồn thì vào dashboard Supabase bấm
  **Restore**. Có pipeline chạy theo lịch thì không bị dừng.
- **Space công khai**: ai cũng xem được code trên trang Space (secrets thì không). Không được để Space ở chế độ private,
  vì khi đó Vercel không gọi được backend.
- **Dagster UI**: `https://<user>-atomesus.hf.space/dagster`, đăng nhập `admin` / mật khẩu Dagster.
- **Log**: trang Space → **Logs** (Build / Container). Web: Vercel → project → **Logs**.
- **Đổi khóa / mật khẩu**: Space → **Settings → Variables and secrets** → sửa, Space tự khởi động lại.
  Lộ OpenAI key: thu hồi trên trang OpenAI, sửa `OPENAI_API_KEY` ở đây.
- **Giới hạn tiền OpenAI**: như mục 4 của [deploy.md](deploy.md) (`policy/budgets.yml` và Budget trên tài khoản OpenAI).

Cần chạy ổn định lâu dài, không ngủ, giữ trọn dữ liệu: dùng một VM theo [deploy.md](deploy.md).
