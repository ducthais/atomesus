"""Day 2 — theo dõi pipeline đã duyệt chạy theo lịch (SPEC §3.4, §9.6). Luồng một vòng, mỗi module một bước:

1. runs.py       thu số đo mỗi lần chạy (Dagster, dbt, warehouse, nguồn) thành chuỗi thời gian theo pipeline
2. detectors.py  4 luật suy giảm, tất định, ngưỡng đọc từ objectives.yml
3. day2.py       hành động: nguồn ngừng cấp → cảnh báo; suy giảm khác → run sửa (src/agents/regression.py),
                 một lần trong cooldown_hours; run sửa xong → gắn cờ hoặc mời người sở hữu vào sửa
4. alerts.py     lưu cảnh báo / cờ (source_stalled, no_improvement, needs_human)

Chạy mỗi phút trong `make dagster` (scripts/dagster_local.py), hoặc gọi tay qua API (src/api/monitor.py).
"""
