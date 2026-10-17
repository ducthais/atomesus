# Weekly Journal — Team P-045 (Atomesus)

> Nhật ký từng tuần: mục tiêu, việc đã xong, khó khăn và cách xử lý, bài học, kế hoạch tuần sau.
> Tổng hợp từ lịch sử Git của repo (commit, PR, merge) và tài liệu quyết định (`docs/adr/`). Số commit đếm như
> [docs/daily-log.md](docs/daily-log.md): mọi nhánh, bỏ commit merge, commit tạm và commit trùng (cherry-pick / rebase).

Thành viên (theo tài khoản GitHub): **ducthais** (Đinh Đức Thái), **HongSon507** (Trần Hồng Sơn), **bobui147**,
**cuongphanhp**.

| Tuần | Thời gian | Trọng tâm | Commit |
|---|---|---|---|
| 1 (W37) | 07/09 – 13/09 | Nhận repo khung, cài hook ghi AI log | 4 |
| 2 (W38) | 14/09 – 20/09 | Worklog, AI log cho Kiro IDE | 2 |
| 3 (W39) | 21/09 – 27/09 | Mock-up giao diện, sửa hook trên Windows | 3 |
| 4 (W40) | 28/09 – 04/10 | SPEC, khung backend/agent/web, chạy thông nguồn Postgres đầu tiên | 50 |
| 5 (W41) | 05/10 – 11/10 | Sprint 2–3: cổng agent, đa nguồn, eval, Day 2, deploy production, deploy qua PR | 66 |
| 6 (W42) | 12/10 – 18/10 | Sprint 4–5: optimizer, kết nối của người dùng, đa người dùng, deploy thẳng, hoàn thiện | 57 |

---

## Tuần 1 (W37): 07/09 – 13/09/2026

### Mục tiêu tuần này
- [x] Nhận repo khung của chương trình (10/09) và thống nhất cách làm việc
- [x] Cài hook ghi prompt AI (yêu cầu của khóa học) chạy được trên Windows
- [x] Bắt đầu worklog hằng ngày

### Đã hoàn thành
- Repo khởi tạo từ mẫu (`phoenix-mentor[bot]`, 10/09).
- HongSon507 cập nhật worklog 11–12/09, sửa hook ghi file UTF-8 **không BOM** trên Windows (Git Bash đọc hỏng file có BOM).

### Khó khăn & Giải pháp
| Khó khăn | Giải pháp | Kết quả |
|---|---|---|
| Hook ghi log trên Windows tạo file UTF-8 có BOM, script Python đọc lỗi | Ghi UTF-8 không BOM | Hook chạy được trên máy Windows của nhóm |

### Bài học
- Phần lớn thành viên dùng Windows: mọi script/hook phải thử trên Windows trước khi dùng chung.

### Kế hoạch tuần sau
- [ ] Chuẩn hóa cấu trúc worklog
- [ ] Cài AI log cho các IDE nhóm dùng

---

## Tuần 2 (W38): 14/09 – 20/09/2026

### Mục tiêu tuần này
- [x] Chuẩn hóa worklog hằng ngày
- [x] Ghi được AI log từ mọi công cụ nhóm dùng

### Đã hoàn thành
- Đinh Đức Thái dựng khung worklog hằng ngày (18/09).
- bobui147 cài ghi AI log cho Kiro IDE (19/09).

### Khó khăn & Giải pháp
| Khó khăn | Giải pháp | Kết quả |
|---|---|---|
| Mỗi người dùng một IDE (Claude Code, Cursor, Kiro, Antigravity…) | Thêm launcher/hook riêng cho từng công cụ, công cụ web ghi tay bằng `scripts/log_manual.py` | Log từ nhiều công cụ về chung `.ai-log/session.jsonl` |

### Bài học
- Cần thống nhất sớm công cụ và quy ước log để không mất dữ liệu chấm điểm.

### Kế hoạch tuần sau
- [ ] Thiết kế giao diện (mock-up)
- [ ] Bắt đầu viết đặc tả

---

## Tuần 3 (W39): 21/09 – 27/09/2026

### Mục tiêu tuần này
- [x] Mock-up giao diện Pipeline Studio
- [x] Hook pre-push gửi AI log chạy ổn định

### Đã hoàn thành
- bobui147: mock-up giao diện (27/09); sửa hook nạp cấu hình Phoenix AI log và ghi hook pre-push không BOM.

### Khó khăn & Giải pháp
| Khó khăn | Giải pháp | Kết quả |
|---|---|---|
| Hook pre-push không đọc được cấu hình gửi log | Nạp biến `AI_LOG_SERVER`, `AI_LOG_API_KEY` từ `.env` trong hook | Push từ thư mục repo chính gửi log được |

### Bài học
- Tiến độ code tuần 1–3 chậm do tập trung vào hạ tầng log; cần chia việc sản phẩm sớm hơn.

### Kế hoạch tuần sau
- [ ] Viết SPEC đầy đủ
- [ ] Dựng khung backend, agent, web và chạy thông một nguồn Postgres

---

## Tuần 4 (W40): 28/09 – 04/10/2026 — Sprint 0–1

### Mục tiêu tuần này
- [x] SPEC: control plane sinh pipeline ELT (18 phase P0–P17, cổng, máy trạng thái, bất biến)
- [x] Hợp đồng dữ liệu `RunReport`, `Spec`, file chính sách
- [x] Agent 4 bước + template Jinja, chạy thử trong sandbox
- [x] Web Pipeline Studio nối backend thật
- [x] Chạy thông 1 nguồn Postgres: yêu cầu → kế hoạch → Spec → code → chạy thử → duyệt → Dagster local

### Đã hoàn thành
- **Đặc tả:** bobui147 và HongSon507 viết `SPEC.md`; HongSon507 thêm hợp đồng Spec, `policy/*.yml` và policy loader.
- **Backend:** HongSon507 hợp đồng `RunReport` + API run mẫu, chat API; Đinh Đức Thái tích hợp pipeline làm sạch
  Postgres với frontend.
- **Agent và sinh code (ducthais):** graph 4 bước + template Jinja; duyệt run và deploy bộ file đã duyệt vào Dagster
  local; gộp sổ bằng chứng vào SQLite; đối chiếu độc lập với nguồn và chạy agent nền; đo thời gian người vs máy.
- **Dữ liệu:** thêm Pagila làm domain `rental`, đóng gói dữ liệu mẫu vào repo.
- **Web:** bobui147 dựng giao diện Pipeline Studio, đăng nhập theo vai; ducthais dựng lại thành Data Catalog, so sánh
  version, xem mapping trước khi chạy, phát hiện schema nguồn thay đổi.
- cuongphanhp thêm môi trường `uv`.

### Khó khăn & Giải pháp
| Khó khăn | Giải pháp | Kết quả |
|---|---|---|
| PR "spec-policy" merge vào `main` làm lệch cấu trúc, phải revert | Làm việc chung trên nhánh `refactor/project-restructure`, merge `-s ours` khi đưa lên `main` | Lịch sử `main` sạch, cây code đúng bản refactor |
| LLM sinh code dễ sai, khó review | Quyết định cốt lõi: **LLM chỉ viết Spec JSON**, code sinh tất định từ template | Code lặp lại được, kiểm bằng cổng tất định |
| Không tin được kết quả pipeline do chính nó tự kiểm | Đối chiếu độc lập: dịch Spec thành SQL thứ hai chạy thẳng trên nguồn | Bằng chứng "khớp từng dòng" trước khi duyệt |

### Bài học
- Chốt hợp đồng dữ liệu (`RunReport`, `Spec`) sớm giúp 4 người làm song song backend / agent / web.
- Tách "người duyệt" khỏi "người tạo" từ đầu, không vá sau.

### Kế hoạch tuần sau
- [ ] Các cổng của agent: semantic, budget, arbitrate, xác nhận kế hoạch
- [ ] Đa nguồn: REST API, CSV; dữ liệu bẩn có chủ đích
- [ ] Bộ chấm điểm (golden) và theo dõi pipeline đã deploy (Day 2)
- [ ] Đưa lên một VM production

---

## Tuần 5 (W41): 05/10 – 11/10/2026 — Sprint 2–3

### Mục tiêu tuần này
- [x] Agent đủ cổng theo SPEC (P2, P3, P5, P8) và vòng tự sửa
- [x] Ghép nhiều nguồn: Postgres + REST API + CSV, dữ liệu bẩn
- [x] Eval 15 ca SPEC §18 và 8 bất biến §19 thành test
- [x] Day 2: ghi số đo mỗi lần pipeline chạy theo lịch
- [x] Deploy production có HTTPS, tên miền riêng
- [x] Deploy qua pull request vào repo pipeline riêng

### Đã hoàn thành
- **Agent (ducthais):** chờ Engineer xác nhận kế hoạch (P2); chặn thực thể hai nghĩa giữa domain (P3); chặn run vượt
  ngân sách run/ngày (P8); chọn lịch đủ tươi rẻ nhất (P5); gộp trước phía "nhiều" của join 1–n; chặn Spec tính sai tổng
  âm thầm; sửa run chạy thử lỗi và chặn giá trị lọc đoán bừa; ngừng gọi LLM khi hết ngân sách ngày; ghi token và USD
  mỗi lần gọi LLM.
- **Nguồn:** đọc nhiều nguồn song song; REST API qua mô tả OpenAPI; thư mục CSV; `sales_raw.orders` bẩn có chủ đích;
  đối chiếu lại khi nguồn là REST API; kiểm khóa nguồn với bronze và chạy lại không đổi (idempotent).
- **Eval:** golden cho 6 ca hợp lệ, bộ chấm `eval/run_eval.py`; HongSon507 chấm đủ 15 ca SPEC §18 → **15/15 đạt**
  (10/10); test 8 bất biến SPEC §19.
- **Vận hành:** màn theo dõi pipeline đã deploy, duyệt nhiều run cùng lúc (ducthais, bobui147); hoàn thiện luồng review
  và vận hành, tối ưu thủ công, đăng ký tài khoản, quản lý tài khoản Admin (bobui147); HongSon507 đo byte nguồn bằng
  metadata Parquet trên Azure Blob Storage.
- **Deploy production (ducthais):** thử Oracle Cloud và combo Vercel + Hugging Face + Supabase, chốt **Azure for
  Students** (VM B2s); tên miền **c4-app-045.id.vn**; header bảo mật, cookie Secure, Caddy restart sau mỗi deploy.
- **Deploy qua PR:** tách Duyệt khỏi Deploy, mở PR vào repo pipeline; service deployer kéo `main`, kiểm manifest, cài vào
  Dagster production; webhook GitHub + callback deployer; giao diện Deploy (link PR, CI, merge); chạy thử sau khi cài,
  đỏ thì quay về bản trước (D2); lịch Dagster tự bật.
- bobui147: landing page Atomesus. cuongphanhp: cập nhật tài liệu kiến trúc.

### Khó khăn & Giải pháp
| Khó khăn | Giải pháp | Kết quả |
|---|---|---|
| Hugging Face bắt gói PRO cho Docker Space (402) | Chuyển sang Azure for Students, 1 VM chạy toàn bộ compose | Production chạy từ 09/10 |
| GitHub Actions của org bị chặn vì billing | Deploy tay bằng `deploy/push.ps1`; sau đó dựng harness có gate + smoke trên máy | Deploy có kiểm tra dù không có CI của org |
| Vòng import làm `make dagster` không chạy | Tách import muộn, thêm test import | Dagster local chạy lại |
| LLM viết Spec cộng tiền sai khi join 1–n (nhân dòng) | Cổng `join_fanout` + gộp trước phía "nhiều" | Ca golden đa nguồn khớp |
| Ngày ghi 3 kiểu trong dữ liệu bẩn | Đọc nhiều định dạng ngày ở staging và màn làm sạch | Dòng hợp lệ không bị loại nhầm |
| Phải merge PR trên GitHub mới lên production | (tuần sau) ADR-0001: deploy thẳng sau khi Reviewer duyệt | — |

### Bài học
- Bằng chứng tự động (đối chiếu, idempotent, dbt test) giúp Reviewer duyệt nhanh và tin được.
- Hạ tầng miễn phí thay đổi nhanh; luôn có phương án dự phòng và tài liệu deploy tay.
- Làm việc nhiều agent AI song song cần "harness": hợp đồng task, worktree riêng, gate tự động, review độc lập.

### Kế hoạch tuần sau
- [ ] Optimizer P13 và tối ưu không gọi LLM
- [ ] Người dùng tự kết nối Postgres của họ
- [ ] Đa người dùng: mỗi người chỉ thấy run của mình, hạn mức, mời Reviewer
- [ ] Deploy thẳng sau khi Reviewer duyệt

---

## Tuần 6 (W42): 12/10 – 18/10/2026 — Sprint 4–5

### Mục tiêu tuần này
- [x] Optimizer P13 và chứng minh cải thiện thời gian so với bản gốc
- [x] Kết nối Postgres của người dùng, tải file CSV/XLSX
- [x] Đa người dùng an toàn: phạm vi xem theo chủ, hạn mức, mời Reviewer
- [x] Deploy thẳng sau khi Reviewer duyệt, giữ GitOps
- [x] Hoàn thiện giao diện, tài liệu, kiểm thử, benchmark

### Đã hoàn thành
- **Agent:** sửa planner (suy domain từ nguồn đã chọn, `concepts.column` trả chuỗi `'null'`); đối chiếu độc lập không bị
  statement timeout (materialize CTE staging); HongSon507: **optimizer P13** và tối ưu thủ công `llm_calls = 0`;
  bobui147: tạo nhiều pipeline cùng lúc + test vòng đời end-to-end.
- **Eval optimizer:** ca 3 triệu dòng (domain `bench`) để optimizer thắng overhead của dbt: **4/4 ca đạt**, UC-3 giữ
  `table_to_incremental` nhanh hơn ~35% (13/10).
- **Kết nối của người dùng (7 task):** lưu kết nối mã hóa, chặn mạng nội bộ; duyệt schema và chọn bảng; sandbox / cổng /
  đối chiếu / Day 2 / Dagster đọc được nguồn `conn:<id>`; lựa chọn thành domain trong chính sách hiệu lực; trang Nguồn dữ
  liệu; hiện IP cần mở firewall.
- **Vận hành:** hàng đợi chạy thử giới hạn sandbox song song cho VM 4 GB.
- **ADR-0001:** một luồng Engineer → Reviewer cho mọi người dùng; deploy thẳng (D6); giữ hai vai và người duyệt diff.
- **Đa người dùng:** mỗi người chỉ thấy run của mình; mời Reviewer bằng link; hạn mức tiền LLM/ngày và số run/ngày; màn
  duyệt có tóm tắt bằng câu dễ hiểu; xem và tải kết quả CSV/XLSX; tải file làm nguồn; deploy thẳng qua hộp thư release.
- **Gia cố production (cả nhóm, 15–16/10):** HongSon507 nguồn `review_api`, chính sách hiệu lực, Dagster runtime hợp
  nhất (reload không dừng job), file DuckDB riêng mỗi pipeline, script dựng VM; bobui147 gia cố workflow production,
  test callback replay và ẩn domain `sales`; cuongphanhp chọn vai nhanh khi đăng nhập, test tải DuckDB theo model;
  ducthais: chỉ Admin xem file chính sách YAML.
- **17–18/10:** bobui147 làm lại giao diện sau đăng nhập; review + sửa (nhãn "Giới hạn", bỏ nhãn online giả, Việt hóa màn
  đăng nhập, layout Nguồn dữ liệu); bật lại PR lưu vết, repo pipeline tự merge khi CI xanh; deploy production `ea7c2c1`;
  cập nhật sơ đồ kiến trúc, ARCHITECTURE, tài liệu kiểm thử, báo cáo đánh giá.
- **Kiểm thử + benchmark (17–18/10):** 1.629 test backend (độ phủ 93%), 28 test tích hợp Postgres, 104 test web, 4 E2E;
  benchmark 20 ca chạy 5 lượt, lượt chính thức **19/20**. Benchmark tìm ra và đã sửa hồi quy thiếu định dạng ngày
  `%Y/%m/%d` (mọi pipeline trên `sales_raw.orders` dừng NEEDS_HUMAN) và lỗi planner hỏi lại yêu cầu rõ; sửa bộ chấm và hai
  bộ test đã cũ (E2E sau nút hiện mật khẩu, `pipeline_tests` sau khi ẩn domain `sales`).

### Khó khăn & Giải pháp
| Khó khăn | Giải pháp | Kết quả |
|---|---|---|
| Optimizer không thắng trên bảng mẫu nhỏ (overhead dbt lớn hơn phần tiết kiệm) | Thêm ca bench 3 triệu dòng sinh lúc chạy; luật nhiễu bỏ 1 mẫu chậm nhất của bản ứng viên | Eval optimizer 4/4, cải thiện thời gian thật |
| Run chưa lọc theo người dùng (lỗ hổng khi mở đăng ký tự do) | Task USER-SCOPE: quyền xem theo chủ, fail closed, 7 vòng review | Mỗi người chỉ thấy run của mình |
| VM 4 GB quá tải khi nhiều sandbox chạy cùng lúc | Hàng đợi `PIPELINE_MAX_PARALLEL_SANDBOX=1` | Không còn hết RAM |
| Deploy lại pipeline làm Dagster khởi động lại, giết job đang chạy | Code location hợp nhất + reload | Deploy không gián đoạn |
| Bấm Deploy phải chờ người merge PR | Deploy thẳng qua hộp thư release; PR chỉ để lưu vết, repo tự merge khi CI xanh | Từ bấm Deploy tới DEPLOYED không cần thao tác GitHub |
| Hạn mức Codex hết giữa chừng | Tạm làm trực tiếp bằng git, giữ nguyên harness để dùng lại | Không chặn tiến độ |
| Benchmark trên Postgres máy dev đẹp hơn thật (volume còn seed sạch cũ, giấu lỗi định dạng ngày) | Nạp lại đúng seed của repo trước khi đo, ghi vào hướng dẫn | Lộ và sửa hồi quy; lượt chính thức 19/20 trên dữ liệu đúng |
| E2E và test tích hợp Postgres cũ đi mà không ai biết (không nằm trong gate) | Sửa test theo hành vi hiện tại; đề xuất đưa vào gate | 4/4 E2E, 28/28 tích hợp |

### Bài học
- Mở cho người dùng thật thì phạm vi dữ liệu theo chủ và hạn mức phải làm **trước** tính năng mới.
- Benchmark phải có dữ liệu đủ lớn mới đo được tối ưu; số đo phải kèm mẫu số và cách đo.
- Giữ bất biến an toàn (app không có quyền merge) và đẩy tự động hóa sang đúng chỗ (CI của repo pipeline).

### Kế hoạch tiếp theo
- [ ] Video demo và pitch deck
- [ ] Lưu pipeline đã deploy vào Chroma để agent học theo (SPEC FR-18)
- [ ] Cloud đầy đủ (DB quản lý thay SQLite) nếu còn ngân sách
