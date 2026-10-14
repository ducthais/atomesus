"""Hạn mức theo từng người dùng (ADR-0001 D10): chi phí (LLM + quét dữ liệu) và số run mới mỗi ngày UTC.

- Chi phí và run cộng cho chủ của run (cùng cách lần chủ của `access.run_owner`): run hệ thống tính cho chủ pipeline.
  Run đã giữ lượt mà chưa vào sổ (mẻ đang khởi tạo) lấy chủ từ bảng run_slots ghi lúc giữ lượt.
- Trần đọc từ budgets.yml `per_user` (NFR-04); trần chung `per_day` vẫn áp ở src/agents/budget.py.
- Admin, run cũ không rõ chủ (LEGACY) và run không lần ra chủ (UNKNOWN) chỉ chịu trần chung.
- Đếm và ghi nằm trong cùng giao dịch BEGIN IMMEDIATE với khoản giữ chỗ / lượt run, nên hai request đồng thời của cùng
  người không cùng vượt trần.
"""

import sqlite3
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

from fastapi import HTTPException

from src.pipeline import store
from src.pipeline.auth import USERS
from src.policy.models import Budgets
from src.run_report.access import LEGACY, UNKNOWN, _owner, run_owner
from src.run_report.store import OwnerGate, _database


def _day(now: datetime) -> str:
    return now.date().isoformat()


def _next_reset(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time.min, UTC)


def limited_owner(store_path: str, owner: str | None) -> str | None:
    """`owner` nếu chịu trần cá nhân; None với Admin, dữ liệu cũ không rõ chủ và chủ không phải tài khoản nào."""
    if owner is LEGACY or owner == UNKNOWN:
        return None
    account = USERS.get(owner) or store.get_user_by_id(store_path, owner)
    if account is None or account["role"] == "Admin":
        return None
    return owner


def run_quota_owner(store_path: str, run_id: str) -> str | None:
    """Chủ chịu trần cá nhân cho chi phí của run `run_id` (None: chỉ áp trần chung)."""
    return limited_owner(store_path, run_owner(store_path, run_id))


class _Owners:
    """Tra chủ của run trên kết nối của giao dịch đang mở, nhớ kết quả.

    Chủ đã ghi (run_owners) và danh sách tài khoản đọc lúc tạo, trước khi mở giao dịch: hai bảng này do
    src.pipeline.store quản lý, mở kết nối của nó trong lúc đang giữ khóa ghi sẽ phải chờ chính mình.
    """

    def __init__(self, store_path: str):
        self.recorded = store.list_run_owners(store_path)
        self.accounts = set(USERS) | {account["id"] for account in store.list_users(store_path)}
        self.cache: dict[str, str | None] = {}

    def of(self, conn: sqlite3.Connection, run_id: str) -> str | None:
        if run_id not in self.cache:

            def lineage(current: str) -> tuple[str | None, str | None] | None:
                row = conn.execute(
                    "SELECT json_extract(report, '$.created_by'), json_extract(report, '$.parent_run_id')"
                    " FROM run_reports WHERE run_id = ?",
                    (current,),
                ).fetchone()
                return (row[0], row[1]) if row else None

            if lineage(run_id) is None:
                row = conn.execute("SELECT owner_id FROM run_slots WHERE run_id = ?", (run_id,)).fetchone()
                self.cache[run_id] = row[0] if row else UNKNOWN
            else:
                self.cache[run_id] = _owner(run_id, lineage, self.accounts.__contains__, self.recorded.get)
        return self.cache[run_id]


def _spent(conn: sqlite3.Connection, owners: _Owners, owner: str, day: str) -> float:
    """Chi phí đã ghi và khoản đang giữ chỗ trong ngày `day` của các run thuộc `owner`."""
    rows = conn.execute(
        "SELECT run_id, sum(usd) FROM ("
        " SELECT run_id, usd FROM budget_ledger WHERE substr(spent_at, 1, 10) = ?"
        " UNION ALL SELECT run_id, usd FROM budget_reservations WHERE substr(reserved_at, 1, 10) = ?"
        ") GROUP BY run_id",
        (day, day),
    ).fetchall()
    return float(sum(total for run_id, total in rows if owners.of(conn, run_id) == owner))


def _runs(conn: sqlite3.Connection, owners: _Owners, owner: str, day: str) -> int:
    """Số run tạo trong ngày `day` (gồm lượt đã giữ, chưa vào sổ) thuộc `owner`."""
    rows = conn.execute(
        "SELECT run_id FROM run_reports WHERE substr(created_at, 1, 10) = ?"
        " UNION SELECT run_id FROM run_slots WHERE substr(reserved_at, 1, 10) = ?",
        (day, day),
    ).fetchall()
    return sum(owners.of(conn, row[0]) == owner for row in rows)


def cost_gate(store_path: str, owner: str, limit: float) -> tuple[OwnerGate, dict]:
    """Cổng trần cá nhân cho store.reserve_budget(_batch): chạy trong giao dịch giữ chỗ. Dict trả kèm nhận
    `spent` (số của `owner` trước khoản này) khi cổng đã chạy, để nơi gọi ghi rõ lý do."""
    owners, seen = _Owners(store_path), {}

    def gate(conn: sqlite3.Connection, usd: float) -> bool:
        seen["spent"] = _spent(conn, owners, owner, _day(datetime.now(UTC)))
        return seen["spent"] + usd <= limit

    return gate, seen


def _exhausted(limit: int, used: int, count: int, now: datetime) -> HTTPException:
    retry = str(max(1, int((_next_reset(now) - now).total_seconds())))
    if count == 1 or used >= limit:
        detail = f"Bạn đã dùng hết {limit} run hôm nay, thử lại sau 0:00 UTC."
    else:
        detail = (
            f"Bạn đã dùng {used}/{limit} run hôm nay, không đủ lượt cho {count} run; "
            "giảm số run hoặc thử lại sau 0:00 UTC."
        )
    return HTTPException(429, detail, headers={"Retry-After": retry})


def claim_runs(store_path: str, user: dict, budgets: Budgets, count: int = 1) -> list[str]:
    """Giữ lượt cho `count` run mới của `user` trước khi gọi LLM hay ghi run; trả run_id đã giữ.

    Hết lượt (budgets.yml per_user.max_runs_per_day) → 429, không giữ lượt nào. Admin không giới hạn theo người dùng.
    """
    run_ids = [str(uuid4()) for _ in range(count)]
    owner = limited_owner(store_path, user["id"])
    if owner is None:
        return run_ids
    limit, owners, now = budgets.per_user.max_runs_per_day, _Owners(store_path), datetime.now(UTC)
    with _database(store_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        used = _runs(conn, owners, owner, _day(now))
        if used + count > limit:
            raise _exhausted(limit, used, count, now)
        conn.executemany(
            "INSERT INTO run_slots (run_id, owner_id, reserved_at) VALUES (?, ?, ?)",
            [(run_id, owner, now.isoformat()) for run_id in run_ids],
        )
    return run_ids


def release_runs(store_path: str, run_ids: list[str]) -> None:
    """Bỏ lượt đã giữ mà sổ không có run nào mang run_id đó: lượt tạm của run vừa vào sổ dưới id khác (run tự được
    đếm), khởi tạo lỗi, mẻ bị chặn ngân sách. Run đã ghi vẫn tính."""
    if not run_ids:
        return
    marks = ", ".join("?" for _ in run_ids)
    with _database(store_path) as conn:
        conn.execute(
            f"DELETE FROM run_slots WHERE run_id IN ({marks}) AND run_id NOT IN (SELECT run_id FROM run_reports)",
            run_ids,
        )


def spent_today(store_path: str, owner: str) -> float:
    """Chi phí hôm nay (đã ghi và đang giữ chỗ) của các run thuộc `owner`."""
    owners = _Owners(store_path)
    with _database(store_path) as conn:
        return _spent(conn, owners, owner, _day(datetime.now(UTC)))


def usage(store_path: str, user: dict, budgets: Budgets) -> dict:
    """Hạn mức hôm nay của chính `user`: đã dùng / trần, tiền và số run. Không chứa số của người khác."""
    now, owners = datetime.now(UTC), _Owners(store_path)
    with _database(store_path) as conn:
        usd = _spent(conn, owners, user["id"], _day(now))
        runs = _runs(conn, owners, user["id"], _day(now))
    limited = limited_owner(store_path, user["id"]) is not None
    usd_limit = budgets.per_user.max_usd_per_day if limited else None
    runs_limit = budgets.per_user.max_runs_per_day if limited else None
    return {
        "day": _day(now),
        "resets_at": _next_reset(now).isoformat(),
        "limited": limited,
        "usd": {
            "used": usd,
            "limit": usd_limit,
            "remaining": max(0.0, usd_limit - usd) if usd_limit is not None else None,
        },
        "runs": {
            "used": runs,
            "limit": runs_limit,
            "remaining": max(0, runs_limit - runs) if runs_limit is not None else None,
        },
    }
