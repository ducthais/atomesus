# Tài liệu dự án — Atomesus (P-045)

Mục lục mọi tài liệu đặc tả, thiết kế, vận hành và kiểm thử. Cột "Trạng thái" cho biết tài liệu mô tả đúng bản đang chạy
(`main`, production https://c4-app-045.id.vn) hay là bản ghi lịch sử của một giai đoạn.

## Đặc tả và thiết kế

| Tài liệu | Nội dung | Trạng thái |
|---|---|---|
| [SPEC.md](../SPEC.md) | Đặc tả gốc: mục tiêu, actor, use case, yêu cầu chức năng / phi chức năng, hợp đồng dữ liệu (Spec, RunReport), 18 phase P0–P17, các cổng, máy trạng thái, agent LangGraph, API, frontend, đo lường, 8 bất biến | Nền tảng; các điểm ADR-0001 thay đổi ghi ở ADR |
| [adr/0001-mot-luong-va-nen-production.md](adr/0001-mot-luong-va-nen-production.md) | ADR-0001: một luồng Engineer → Reviewer cho mọi người dùng; deploy thẳng sau khi duyệt (D6); phạm vi theo chủ; hạn mức; kết nối Postgres của người dùng; bất biến sau khi đổi | Hiện hành (chốt 2026-10-13) |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Mô tả thành phần, luồng dữ liệu, máy trạng thái, triển khai, bảo mật, bảng quyết định thiết kế | Hiện hành (2026-10-18) |
| [architecture_diagram.md](architecture_diagram.md) | 9 sơ đồ Mermaid: ngữ cảnh, thành phần, production, agent, end-to-end, sandbox + đối chiếu, trạng thái run, deploy + GitOps, ranh giới bảo mật; bảng tra module | Hiện hành (2026-10-18) |
| [system_architecture_analysis.md](system_architecture_analysis.md) | Phân tích luồng và sơ đồ bản 1.0 | Lịch sử (2026-10-03, trước deployer và đa người dùng) |
| [deploy-pr.md](deploy-pr.md) | README của repo pipeline: cấu trúc, CI kiểm manifest + chữ ký, quy trình PR | Hiện hành (chế độ GitOps) |
| [task-b.md](task-b.md), [task-c.md](task-c.md) | Ghi chú triển khai Sprint 0–1 (Postgres → làm sạch → frontend; lõi AI 4 node) | Lịch sử |
| `../policy/*.yml` | Chính sách chạy: `domains.yml` (nguồn, thực thể, steward), `budgets.yml` (trần chi phí, LLM, hạn mức người dùng), `objectives.yml` (độ tươi) | Hiện hành |
| `../src/spec/schemas/` | JSON Schema của Spec | Hiện hành |

## Vận hành và triển khai

| Tài liệu | Nội dung | Trạng thái |
|---|---|---|
| [deploy.md](deploy.md) | Dựng VM, cấu hình `.env`, deploy, chế độ deploy `direct` / `pr`, deployer, sự cố thường gặp | Hiện hành |
| [deploy-free.md](deploy-free.md) | Phương án Vercel + Hugging Face + Supabase | Không dùng (Hugging Face bắt gói PRO) |
| [demo.md](demo.md) | Kịch bản demo MVP Sprint 2 | Lịch sử: bước duyệt đã tách thành Duyệt + Deploy |
| [time-study.md](time-study.md) | Quy trình bấm giờ làm tay so với dùng agent | Hiện hành |

## Kiểm thử và đánh giá

| Tài liệu | Nội dung |
|---|---|
| [testing.md](testing.md) | Chiến lược, các tầng test, bộ test theo module, 8 bất biến, test giao diện, benchmark 20 ca, gate + smoke, cách chạy, kết quả, giới hạn |
| [../eval/results/report.md](../eval/results/report.md) | Báo cáo đánh giá theo mẫu BTC: metrics, kết quả test, optimizer, phản hồi người dùng, việc cần làm |
| [../eval/results/latest.md](../eval/results/latest.md) | Bảng từng ca của lượt benchmark đầy đủ gần nhất (`latest.json` là số liệu thô) |

## Quản lý dự án

| Tài liệu | Nội dung |
|---|---|
| [../JOURNAL.md](../JOURNAL.md) | Nhật ký tuần W37–W42: mục tiêu, hoàn thành, khó khăn & giải pháp, bài học, kế hoạch |
| [daily-log.md](daily-log.md) | Worklog theo ngày: ai làm gì, commit nào |
| `../.ai-log/` | AI log (hook ghi prompt của các công cụ AI, gửi lên server chấm ở mỗi lần push) |

## Tài liệu hướng dẫn của chương trình

`guide/` là tài liệu tham khảo do chương trình cung cấp kèm repo mẫu (chương học, checklist deliverables, mẫu code),
không phải tài liệu của sản phẩm.
