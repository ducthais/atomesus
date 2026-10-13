"""Sinh tất định bộ sự kiện lớn cho ca optimizer: mặc định 5 triệu dòng, ghi CSV vào infra/landing/bench/events/.

Dữ liệu sinh lúc chạy, không commit (thư mục đã .gitignore). Mỗi dòng tính thẳng từ số thứ tự (không dùng `random`) nên
cùng `--rows` luôn ra cùng một file, trên mọi máy và mọi bản Python.

| Cột          | Giá trị                                                   |
|--------------|-----------------------------------------------------------|
| `event_id`   | 1 … rows                                                  |
| `event_date` | trải đều 365 ngày từ 2025-01-01 đến 2025-12-31            |
| `user_id`    | 1 … 200.000 (một người dùng có nhiều sự kiện)             |
| `amount`     | số tiền VND, bội của 1.000, từ 10.000 đến 999.000         |

Vì sao cần lớn: với 1 triệu dòng, chạy lại model gold dạng table (0,151 s) và incremental (0,147 s) gần như bằng nhau vì
overhead dbt lấn át; với 3 triệu dòng, incremental nhanh hơn ~26% (0,213 s vs 0,158 s), với 5 triệu dòng ~35%, hai dải
đo không chồng nhau (đo dbt-duckdb, group by ngày + count distinct + sum). Ca eval dùng `--rows 3000000` để bước el_run
nạp bronze xong trong giới hạn thời gian.

Chạy lại khi file đã đủ đúng số dòng thì không sinh lại. Chạy: python eval/bench/make_events.py [--rows N] [--out DIR]
"""

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVENTS_DIR = ROOT / "infra" / "landing" / "bench" / "events"
FILE_NAME = "events.csv"
DEFAULT_ROWS = 5_000_000
HEADER = ("event_id", "event_date", "user_id", "amount")
FIRST_DAY, DAYS, USERS = date(2025, 1, 1), 365, 200_000
DAY_TEXT = [(FIRST_DAY + timedelta(days=d)).isoformat() for d in range(DAYS)]


def row(i: int) -> tuple:
    """Dòng thứ `i` (từ 0). Nhân với số nguyên tố lớn rồi lấy dư để ngày, người dùng, số tiền không đi theo thứ tự."""
    return (
        i + 1,
        DAY_TEXT[(i * 7_919) % DAYS],
        (i * 104_729) % USERS + 1,
        ((i * 1_299_709) % 990 + 10) * 1_000,
    )


def existing_rows(path: Path) -> int | None:
    """Số dòng dữ liệu (không tính tiêu đề) của file đã có, None nếu chưa có file."""
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def ensure_events(rows: int = DEFAULT_ROWS, out: Path = EVENTS_DIR) -> bool:
    """Sinh `out/events.csv` đúng `rows` dòng nếu chưa có hoặc khác số dòng. Trả True khi vừa sinh, False khi bỏ qua."""
    if rows < 1:
        raise ValueError("Số dòng phải lớn hơn 0.")
    path = out / FILE_NAME
    if existing_rows(path) == rows:
        return False
    out.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".tmp")  # không khớp *.csv: nguồn filesystem không đọc nhầm file đang ghi dở
    with partial.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(row(i) for i in range(rows))
    partial.replace(path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help=f"số dòng sự kiện (mặc định {DEFAULT_ROWS:,})")
    parser.add_argument("--out", type=Path, default=EVENTS_DIR, help="thư mục ghi events.csv")
    args = parser.parse_args()
    if args.rows < 1:
        parser.error("--rows phải lớn hơn 0")
    made = ensure_events(args.rows, args.out)
    verdict = "đã sinh" if made else "đã có đủ, bỏ qua"
    print(f"{args.out / FILE_NAME}: {args.rows:,} dòng, {verdict}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
