"""Ai thấy run nào (ADR-0001 D1, D2, bất biến 9). Người dùng luôn lấy từ phiên đăng nhập, không bao giờ từ client.

- Admin thấy mọi run.
- Engineer thấy run do chính mình tạo.
- Reviewer thấy run của các Engineer đã mời mình (`reviewer_links`); cặp demo minh ↔ an luôn coi là đã liên kết.
- Run hệ thống tạo (`created_by = "system"`: regression / Day 2) thuộc về chủ của run gốc, lần theo `parent_run_id`.
- Run không xác định được chủ (dữ liệu cũ: người tạo không phải tài khoản nào) chỉ Admin và cặp demo thấy.

Không thấy thì API trả 404 như run không tồn tại, để không lộ run của người khác có hay không.
"""

from dataclasses import dataclass

from fastapi import HTTPException

from src.pipeline import store
from src.pipeline.auth import DEMO_ENGINEER, DEMO_REVIEWER, USERS
from src.run_report.models import RunReport
from src.run_report.store import get_run_report, list_run_lineage

SYSTEM = "system"
MAX_LINEAGE = 100  # chặn vòng lặp nếu parent_run_id lỡ trỏ vòng


@dataclass(frozen=True)
class Scope:
    owners: frozenset[str] | None  # None: Admin, thấy mọi run
    legacy: bool  # thấy run không có chủ

    def allows(self, owner: str | None) -> bool:
        if self.owners is None:
            return True
        return self.legacy if owner is None else owner in self.owners


def linked_engineers(store_path: str, reviewer_id: str) -> set[str]:
    """Engineer mà Reviewer được duyệt run; gồm cặp demo không ghi trong DB."""
    linked = store.linked_engineers(store_path, reviewer_id)
    return (linked | {DEMO_ENGINEER}) if reviewer_id == DEMO_REVIEWER else linked


def scope(user: dict, store_path: str) -> Scope:
    if user["role"] == "Admin":
        return Scope(None, True)
    legacy = user["id"] in (DEMO_ENGINEER, DEMO_REVIEWER)
    if user["role"] == "Engineer":
        return Scope(frozenset({user["id"]}), legacy)
    if user["role"] == "Reviewer":
        # Cũng thấy run do chính mình tạo (nếu có), để luật "không tự duyệt" (FR-01) báo đúng lý do.
        return Scope(frozenset(linked_engineers(store_path, user["id"]) | {user["id"]}), legacy)
    return Scope(frozenset({user["id"]}), False)


def _account(store_path: str, user_id: str) -> bool:
    return user_id in USERS or store.get_user_by_id(store_path, user_id) is not None


def run_owner(store_path: str, report: RunReport) -> str | None:
    """Chủ của run: người tạo, hoặc chủ của run gốc với run hệ thống; None nếu không xác định được."""
    current: RunReport | None = report
    for _ in range(MAX_LINEAGE):
        if current is None:
            return None
        if current.created_by != SYSTEM:
            return current.created_by if _account(store_path, current.created_by) else None
        if not current.parent_run_id or current.parent_run_id == current.run_id:
            return None
        current = get_run_report(store_path, current.parent_run_id)
    return None


def can_view_run(user: dict, report: RunReport, store_path: str) -> bool:
    access = scope(user, store_path)
    return access.owners is None or access.allows(run_owner(store_path, report))


def visible_run_ids(user: dict, store_path: str) -> set[str] | None:
    """run_id mà `user` thấy; None = mọi run (Admin). Đọc metadata một lần cho cả danh sách."""
    access = scope(user, store_path)
    if access.owners is None:
        return None
    lineage = list_run_lineage(store_path)
    accounts = set(USERS) | {account["id"] for account in store.list_users(store_path)}

    def owner(run_id: str) -> str | None:
        current = run_id
        for _ in range(MAX_LINEAGE):
            if current not in lineage:
                return None
            created_by, parent = lineage[current]
            if created_by != SYSTEM:
                return created_by if created_by in accounts else None
            if not parent or parent == current:
                return None
            current = parent
        return None

    return {run_id for run_id in lineage if access.allows(owner(run_id))}


def viewable_run(store_path: str, run_id: str, user: dict) -> RunReport:
    """RunReport mà `user` được thấy; không có hoặc không được thấy đều là 404 cùng một thông báo."""
    report = get_run_report(store_path, run_id)
    if report is None or not can_view_run(user, report, store_path):
        raise HTTPException(404, f"Không tìm thấy run {run_id}")
    return report
