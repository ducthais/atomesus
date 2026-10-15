"""Sinh dữ liệu cho nguồn `review_api` (mock API `/reviews/feedback`): phản hồi khách gửi sau khi trả DVD ở Pagila.

Đọc thẳng file seed Pagila có sẵn (infra/postgres/init/003_pagila.sql.gz), không cần Postgres đang chạy, nên khóa luôn
khớp dữ liệu thật:
- `rental_id` là lượt thuê có thật đã trả (`return_date` khác NULL), `customer_id` đúng khách của lượt đó,
- `film_id` suy ra qua `inventory`, `submitted_at` sau `return_date` từ 30 phút đến 7 ngày.

Khoảng 15% lượt thuê đã trả có phản hồi (tỷ lệ trả lời khảo sát thường gặp). Dữ liệu bẩn có chủ đích, giữ tổng số dòng
bị tách dưới `objectives.yml: rental.quality.max_reject_pct` (1%):
- khoảng 1% bản ghi gửi lại y hệt (cùng `feedback_id`: dedupe / merge phải gộp),
- khoảng 0,5% `rating = "N/A"` (ép kiểu thất bại → bảng rejected),
- khoảng 0,3% phản hồi trỏ tới `rental_id` không có trong Pagila (LEFT JOIN không khớp, không phải lỗi staging).

Seed ngẫu nhiên cố định: chạy lại cho đúng cùng một file. Chạy: python scripts/build_review_data.py
"""

import gzip
import json
import random
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / "infra" / "postgres" / "init" / "003_pagila.sql.gz"
OUTPUT = ROOT / "infra" / "mock_api" / "fixtures" / "rental_feedback.json.gz"
SEED = 45
RESPONSE_RATE = 0.15
DUPLICATE_RATE = 0.01
NA_RATE = 0.005
ORPHAN_RATE = 0.003
RATINGS, WEIGHTS = (1, 2, 3, 4, 5), (4, 8, 18, 38, 32)
COMMENTS = {
    1: ("Đĩa bị xước, không xem được", "Rất thất vọng"),
    2: ("Phim chán hơn mong đợi", "Hình hơi mờ"),
    3: ("Tạm được", "Bình thường"),
    4: ("Phim hay, sẽ thuê lại", "Đĩa tốt, giao đúng hẹn"),
    5: ("Tuyệt vời!", "Rất đáng xem, giới thiệu cho bạn bè"),
}


def read_dump() -> tuple[list[dict], dict[int, int]]:
    """Mọi lượt thuê (`returned` None nếu chưa trả) và bảng inventory_id → film_id, đọc từ các khối COPY của file seed.

    eval/golden/build.py dùng lại để tính số lượt thuê theo khách."""
    rentals, films, current = [], {}, None
    with gzip.open(DUMP, "rt", encoding="utf-8") as dump:
        for line in dump:
            found = re.match(r"COPY (\S+) \(", line)
            if found:
                current = found.group(1)
                continue
            if current and line.startswith("\\."):
                current = None
                continue
            parts = line.rstrip("\n").split("\t")
            if current == "pagila.inventory":
                films[int(parts[0])] = int(parts[1])
            elif current == "pagila.rental":
                returned = None if parts[4] == "\\N" else datetime.fromisoformat(parts[4].replace("+00", "+00:00"))
                rentals.append(
                    {
                        "rental_id": int(parts[0]),
                        "inventory_id": int(parts[2]),
                        "customer_id": int(parts[3]),
                        "returned": returned,
                    }
                )
    return sorted(rentals, key=lambda r: r["rental_id"]), films


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build() -> list[dict]:
    rng = random.Random(SEED)
    all_rentals, films = read_dump()
    rentals = [r for r in all_rentals if r["returned"] is not None]  # chỉ lượt đã trả mới có phản hồi
    rows = []
    for rental in rentals:
        if rng.random() >= RESPONSE_RATE:
            continue
        rating = rng.choices(RATINGS, WEIGHTS)[0]
        recommend = rating >= 4 if rng.random() > 0.05 else rating < 4  # vài người chấm cao vẫn không giới thiệu
        rows.append(
            {
                "feedback_id": f"FB-{len(rows) + 1:06d}",
                "rental_id": rental["rental_id"],
                "customer_id": rental["customer_id"],
                "film_id": films[rental["inventory_id"]],
                "rating": str(rating),
                "would_recommend": recommend,
                "comment": rng.choice(COMMENTS[rating]),
                "submitted_at": _iso(rental["returned"] + timedelta(minutes=rng.randint(30, 7 * 24 * 60))),
            }
        )
    top = max(r["rental_id"] for r in rentals)
    for row in rng.sample(rows, round(len(rows) * NA_RATE)):
        row["rating"] = "N/A"
    for index, row in enumerate(rng.sample(rows, round(len(rows) * ORPHAN_RATE)), start=1):
        row["rental_id"] = top + 1000 + index  # lượt thuê không có ở Pagila
    for row in rng.sample(rows, round(len(rows) * DUPLICATE_RATE)):
        rows.insert(rows.index(row) + 1, dict(row))  # gửi lại nguyên bản ghi ngay sau bản gốc
    return rows


def main() -> int:
    rows = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with OUTPUT.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:  # mtime=0: file y hệt mỗi lần
        out.write(body)
    unique = len({r["feedback_id"] for r in rows})
    print(f"{len(rows):,} bản ghi ({unique:,} feedback_id) → {OUTPUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
