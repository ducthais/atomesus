"""Làm dữ liệu nguồn to dần để pipeline đã lên lịch chậm lại (demo phát hiện suy giảm, Day 2).

Mỗi lần chạy thêm vào cả ba nguồn của domain sales, giữ kiểu dữ liệu bẩn như seed:
- Postgres `sales_raw.orders`: N đơn mới nối tiếp order_id lớn nhất (cùng luật ghi ngày / số tiền của 001_seed.sql),
  ghi bằng tài khoản admin trong container như scripts/seed_pipeline.py (tài khoản của pipeline chỉ đọc);
- mock API: 4 khoản thanh toán cho mỗi đơn mới (endpoint quản trị `/sales/_admin/grow`, không có trong OpenAPI);
  mock API giữ dữ liệu trong bộ nhớ nên khởi động lại là về seed;
- CSV: một file hoàn tiền mới `refunds/refunds_grow_<n>.csv`.

Chạy: python scripts/grow_data.py --orders 5000  (thêm --skip-api / --skip-csv để bỏ nguồn tương ứng)
"""

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COMPOSE = ["docker", "compose", "--env-file", ".env", "-p", "p045-b", "-f", "infra/postgres/compose.yml"]
PSQL = ["psql", "-U", "pipeline_admin", "-d", "pipeline_demo", "-v", "ON_ERROR_STOP=1", "-tA"]
REFUNDS_DIR = ROOT / "infra" / "landing" / "sales" / "refunds"


def orders_sql(count: int) -> str:
    """Câu SQL thêm `count` đơn nối tiếp, cùng luật bẩn của seed (ngày 3 kiểu, số tiền có khoảng trắng / đuôi .00)."""
    return f"""
BEGIN;
WITH start AS (SELECT coalesce(max(order_id), 0) AS base FROM sales_raw.orders)
INSERT INTO sales_raw.orders
SELECT i, ((i - 1) % 10) + 1,
       CASE WHEN i % 4 = 0 THEN ' ' || (i % 1000 * 1000) || ' '
            WHEN i % 7 = 0 THEN (i % 1000 * 1000) || '.00'
            ELSE (i % 1000 * 1000)::text END,
       CASE WHEN i % 3 = 0 THEN to_char(DATE '2026-01-01' + (i % 365), 'DD/MM/YYYY')
            WHEN i % 5 = 0 THEN to_char(DATE '2026-01-01' + (i % 365), 'YYYY/MM/DD')
            ELSE (DATE '2026-01-01' + (i % 365))::text END,
       'paid'
FROM start, generate_series(start.base + 1, start.base + {int(count)}) AS i;
SELECT min(order_id), max(order_id) FROM sales_raw.orders
WHERE order_id > (SELECT max(order_id) - {int(count)} FROM sales_raw.orders);
COMMIT;
"""


def grow_postgres(count: int) -> tuple[int, int]:
    done = subprocess.run(
        [*COMPOSE, "exec", "-T", "postgres", *PSQL], cwd=ROOT, input=orders_sql(count).encode(), capture_output=True
    )
    if done.returncode != 0:
        raise SystemExit(f"Thêm đơn vào Postgres thất bại: {done.stderr.decode(errors='replace')[-500:]}")
    line = next(line for line in done.stdout.decode().splitlines() if "|" in line)
    first, last = (int(x) for x in line.split("|"))
    return first, last


def grow_api(base_url: str, first_order: int, count: int) -> int:
    response = httpx.post(
        f"{base_url.rstrip('/')}/sales/_admin/grow", params={"first_order": first_order, "orders": count}
    )
    response.raise_for_status()
    return response.json()["payments"]


def refunds_csv(first_order: int, count: int, first_refund: int) -> str:
    """Mỗi đơn chia hết cho 10 có một khoản hoàn; ngày ghi DD/MM/YYYY như file tháng 2."""
    lines = ["refund_id,order_id,amount,currency,refunded_at,reason"]
    refund = first_refund
    for order in range(first_order, first_order + count):
        if order % 10 == 0:
            lines.append(f"{refund},{order},{order % 1000 * 100},VND,{1 + order % 28:02d}/03/2026,damaged")
            refund += 1
    return "\n".join(lines) + "\n"


def grow_csv(first_order: int, count: int, folder: Path = REFUNDS_DIR) -> Path:
    existing = sorted(folder.glob("refunds_grow_*.csv"))
    max_refund = 0
    for path in folder.glob("*.csv"):
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            max_refund = max(max_refund, int(line.split(",", 1)[0]))
    path = folder / f"refunds_grow_{len(existing) + 1:03d}.csv"
    path.write_text(refunds_csv(first_order, count, max_refund + 1), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--orders", type=int, default=1000, help="số đơn thêm vào mỗi lần (mặc định 1000)")
    parser.add_argument("--api-url", default="http://127.0.0.1:8100", help="địa chỉ mock API")
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--skip-csv", action="store_true")
    args = parser.parse_args()
    if args.orders <= 0:
        parser.error("--orders phải dương")
    first, last = grow_postgres(args.orders)
    print(f"Postgres: thêm đơn {first}..{last}")
    if not args.skip_api:
        print(f"Mock API: thêm {grow_api(args.api_url, first, args.orders)} khoản thanh toán")
    if not args.skip_csv:
        print(f"CSV: ghi {grow_csv(first, args.orders).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
