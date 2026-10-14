"""Ai thấy run nào (ADR-0001 D1, D2, bất biến 9). Người dùng luôn lấy từ phiên đăng nhập, không bao giờ từ client.

- Admin thấy mọi run.
- Engineer thấy run do chính mình tạo.
- Reviewer thấy run của các Engineer đã mời mình (`reviewer_links`); cặp demo minh ↔ an luôn coi là đã liên kết.
- Run hệ thống tạo (`created_by = "system"`: regression / Day 2) thuộc về chủ của run gốc: đọc chủ đã ghi lúc tạo
  (bảng run_owners), run hệ thống cũ chưa ghi thì lần theo `parent_run_id` tới hết chuỗi.
- Run cũ (run gốc không phải run hệ thống, thiếu `created_by` hoặc người tạo không phải tài khoản nào) có chủ là
  `LEGACY` (None), tính lúc đọc, không ghi vào run: chỉ Admin và cặp demo minh / an thấy. Reviewer khác dù được minh
  mời cũng không thấy, vì run cũ không phải run của minh.
- Run hệ thống không lần ra run gốc (run cha không còn, thiếu `parent_run_id`, `parent_run_id` trỏ vòng) có chủ là
  `UNKNOWN`, đóng hẳn: chỉ Admin thấy, không ai duyệt hay deploy được (deploy không giữ được tên model).
- Dữ liệu theo tên model (monitor, Dagster, bảng gold) thuộc chủ của tên model (`model_owner`); deploy giữ tên model
  trong bảng model_owners trước khi mở pull request, trùng tên của chủ khác bị chặn. Run cũ giữ tên cho
  `LEGACY_OWNER` (chỉ để giữ tên, không cấp quyền xem: run deploy cũ trong sổ vẫn chặn người ngoài cặp demo).
- Domain nguồn riêng của người dùng (catalog, /policy) chỉ chủ, Reviewer đã liên kết và Admin thấy (`policy_for`).

Không thấy thì API trả 404 như run không tồn tại, để không lộ run của người khác có hay không.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException

from src.pipeline import store
from src.pipeline.auth import DEMO_ENGINEER, DEMO_REVIEWER, USERS
from src.pipeline.settings import PipelineSettings
from src.policy import effective_policy
from src.policy.models import Policy
from src.run_report.models import RunReport
from src.run_report.store import get_run_lineage, get_run_report, list_run_lineage, model_deployments

SYSTEM = "system"
LEGACY = None  # chủ của dữ liệu cũ không rõ người tạo
# Chủ của run hệ thống không lần ra run gốc: không phải run cũ, không ai ngoài Admin thấy. Không trùng id tài khoản
# nào (uuid hoặc tên tài khoản hệ thống, không có khoảng trắng).
UNKNOWN = "(không rõ chủ)"
# Tài khoản đứng tên dữ liệu cũ khi cần một chủ cụ thể: giữ tên model (model_owners), domain cho agent. Không cấp quyền.
LEGACY_OWNER = DEMO_ENGINEER


@dataclass(frozen=True)
class Scope:
    owners: frozenset[str] | None  # None: Admin, thấy mọi run
    legacy: bool = False  # thấy dữ liệu cũ không rõ chủ (Admin và cặp demo)

    def allows(self, owner: str | None) -> bool:
        if self.owners is None:
            return True
        if owner == UNKNOWN:
            return False
        return self.legacy if owner is LEGACY else owner in self.owners


def linked_engineers(store_path: str, reviewer_id: str) -> set[str]:
    """Engineer mà Reviewer được duyệt run; gồm cặp demo không ghi trong DB."""
    linked = store.linked_engineers(store_path, reviewer_id)
    return (linked | {DEMO_ENGINEER}) if reviewer_id == DEMO_REVIEWER else linked


def scope(user: dict, store_path: str) -> Scope:
    if user["role"] == "Admin":
        return Scope(None)
    if user["role"] == "Engineer":
        return Scope(frozenset({user["id"]}), legacy=user["id"] == DEMO_ENGINEER)
    if user["role"] == "Reviewer":
        # Cũng thấy run do chính mình tạo (nếu có), để luật "không tự duyệt" (FR-01) báo đúng lý do.
        owners = frozenset(linked_engineers(store_path, user["id"]) | {user["id"]})
        return Scope(owners, legacy=user["id"] == DEMO_REVIEWER)
    return Scope(frozenset({user["id"]}))


def is_account(store_path: str, user_id: str | None) -> bool:
    return bool(user_id) and (user_id in USERS or store.get_user_by_id(store_path, user_id) is not None)


def owner_of(store_path: str, user_id: str | None) -> str | None:
    """Chủ ứng với id người dùng đã lưu (cảnh báo, run): id không phải tài khoản nào là dữ liệu cũ (LEGACY); chủ
    UNKNOWN (cảnh báo của pipeline không lần ra run gốc) giữ nguyên UNKNOWN."""
    if user_id == UNKNOWN:
        return UNKNOWN
    return user_id if is_account(store_path, user_id) else LEGACY


Lineage = Callable[[str], tuple[str | None, str | None] | None]  # run_id → (created_by, parent_run_id)
Recorded = Callable[[str], str | None]  # run_id → chủ đã ghi lúc hệ thống tạo run (bảng run_owners)


def _owner(run_id: str, lineage: Lineage, account: Callable[[str | None], bool], recorded: Recorded) -> str | None:
    """Lần theo metadata (không dựng RunReport, nên bản ghi cũ thiếu trường cũng không lỗi) tới hết chuỗi, chỉ dừng
    khi gặp lại run đã đi qua. Ba kết quả: tài khoản chủ; LEGACY khi run gốc không phải run hệ thống mà thiếu
    `created_by` hoặc người tạo không phải tài khoản; UNKNOWN khi run hệ thống mất run gốc hoặc chuỗi trỏ vòng."""
    current, seen = run_id, set()
    while current not in seen:
        seen.add(current)
        found = lineage(current)
        if found is None:
            return UNKNOWN
        created_by, parent = found
        if created_by != SYSTEM:
            return created_by if account(created_by) else LEGACY
        if owner := recorded(current):
            return owner
        if not parent:
            return UNKNOWN
        current = parent
    return UNKNOWN


def run_owner(store_path: str, run_id: str) -> str | None:
    """Chủ của run: người tạo, hoặc chủ của run gốc với run hệ thống; LEGACY (None) với run cũ, UNKNOWN nếu không lần
    ra run gốc."""
    return _owner(
        run_id,
        lambda current: get_run_lineage(store_path, current),
        lambda uid: is_account(store_path, uid),
        lambda current: store.get_run_owner(store_path, current),
    )


def record_system_run_owner(store_path: str, run_id: str, parent_run_id: str) -> None:
    """Ghi sẵn chủ của run hệ thống vừa tạo từ `parent_run_id` (Day 2), để lần sau đọc thẳng, không phải lần theo
    chuỗi run sửa nối nhau. Chủ là LEGACY hay UNKNOWN thì không ghi: lần theo vẫn ra đúng kết quả đó."""
    owner = run_owner(store_path, parent_run_id)
    if owner not in (LEGACY, UNKNOWN):
        store.record_run_owner(store_path, run_id, owner, time.time())


def is_legacy(store_path: str, run_id: str) -> bool:
    """Run cũ không rõ chủ (kể cả run hệ thống sinh từ run cũ); tính lúc đọc, không lưu vào run."""
    return run_owner(store_path, run_id) is LEGACY


def run_visible(user: dict, store_path: str, run_id: str) -> bool:
    """`user` thấy run `run_id` không; run không tồn tại cũng là False. Chỉ đọc metadata quyền."""
    if get_run_lineage(store_path, run_id) is None:
        return False
    access = scope(user, store_path)
    return access.owners is None or access.allows(run_owner(store_path, run_id))


def can_view_run(user: dict, report: RunReport, store_path: str) -> bool:
    return run_visible(user, store_path, report.run_id)


def visible_run_ids(user: dict, store_path: str) -> set[str] | None:
    """run_id mà `user` thấy; None = mọi run (Admin). Đọc metadata một lần cho cả danh sách."""
    access = scope(user, store_path)
    if access.owners is None:
        return None
    lineage, recorded = list_run_lineage(store_path), store.list_run_owners(store_path)
    accounts = set(USERS) | {account["id"] for account in store.list_users(store_path)}
    return {
        run_id for run_id in lineage if access.allows(_owner(run_id, lineage.get, accounts.__contains__, recorded.get))
    }


def is_own_run(store_path: str, report: RunReport, user: dict) -> bool:
    """Run của chính `user` (FR-01 không tự duyệt): xét chủ đã lần theo run gốc, vì run hệ thống tạo ghi `system`."""
    return user["id"] in (report.created_by, run_owner(store_path, report.run_id))


def model_owners(store_path: str, model: str) -> list[str | None]:
    """Người đã giữ tên `model` (bảng model_owners) rồi tới chủ (đã lần theo run gốc; LEGACY với run cũ) của mọi run
    đã đưa `model` lên production, cũ trước. Run deploy trước khi có bảng giữ tên vẫn được tính."""
    claimed = store.get_model_owner(store_path, model)
    deployed = [run_owner(store_path, run_id) for run_id in model_deployments(store_path, model)]
    return [claimed, *deployed] if claimed else deployed


def _holder(owner: str | None) -> str:
    """Tài khoản giữ tên model thay cho `owner`: dữ liệu cũ giữ tên cho LEGACY_OWNER."""
    return LEGACY_OWNER if owner is LEGACY else owner


def model_owner(store_path: str, model: str) -> str | None:
    """Tên model là không gian tên chung (job Dagster, số đo monitor, bảng gold trong warehouse), nên mỗi tên chỉ thuộc
    một chủ: người đã giữ tên khi deploy, dữ liệu cũ thì chủ của run đầu tiên đưa model lên production (LEGACY_OWNER
    với run cũ). None: chưa ai deploy."""
    owners = model_owners(store_path, model)
    return _holder(owners[0]) if owners else None


def model_visible(user: dict, store_path: str, model: str) -> bool:
    """`user` thấy dữ liệu theo tên `model` (lịch sử chạy, số đo, cảnh báo, bảng gold) không. Model chưa ai giữ tên và
    không có run deploy nào trong sổ là dữ liệu cũ không rõ chủ (LEGACY) như run cũ; model có run deploy cũ cũng vậy,
    dù tên đã giữ cho LEGACY_OWNER. Dữ liệu lẫn nhiều chủ thì chỉ thấy khi được thấy mọi chủ (đóng khi không chắc)."""
    access = scope(user, store_path)
    return all(access.allows(owner) for owner in model_owners(store_path, model) or [LEGACY])


def claim_model(store_path: str, report: RunReport) -> str | None:
    """Giữ tên model của `report` cho chủ run, gọi trước mọi tác động ra ngoài (GitHub, deployer). Trả tên model nếu
    đã thuộc chủ khác (deploy sẽ ghi đè pipeline người khác), không thì None. Hai chủ deploy cùng lúc chỉ một người
    giữ được tên (khóa chính model_owners); deploy lỗi sau đó vẫn giữ tên cho người đã giữ. Run cũ cũng giữ tên, cho
    LEGACY_OWNER (chỉ giữ tên: quyền xem run cũ vẫn theo LEGACY). Run không lần ra chủ (UNKNOWN) không giữ được tên."""
    model = ((report.spec or {}).get("transform") or {}).get("model_name")
    if not model:
        return None
    holder = _holder(run_owner(store_path, report.run_id))
    if holder == UNKNOWN:
        return model
    if any(_holder(other) != holder for other in model_owners(store_path, model)):
        return model
    return None if store.claim_model(store_path, model, holder, time.time()) == holder else model


def policy_for(user: dict, config: PipelineSettings) -> Policy:
    """Chính sách hiệu lực người dùng được xem: Admin thấy domain của mọi người; Engineer thấy domain YAML và domain
    của mình; Reviewer (và vai khác) chỉ thấy thêm domain của các Engineer đã liên kết. Hệ thống xử lý nội bộ vẫn gọi
    effective_policy(config) không lọc."""
    if user["role"] == "Admin":
        return effective_policy(config)
    if user["role"] == "Engineer":
        return effective_policy(config, owner_id=user["id"])
    owners = scope(user, config.store_path).owners or frozenset()
    policy = effective_policy(config)
    hidden = [
        name
        for name, domain in policy.domains.domains.items()
        if domain.owner_id is not None and domain.owner_id not in owners
    ]
    if not hidden:
        return policy
    raw = policy.model_dump()
    for name in hidden:
        raw["domains"]["domains"].pop(name)
        raw["objectives"].pop(name, None)
    return Policy.model_validate(raw)


def viewable_run(store_path: str, run_id: str, user: dict) -> RunReport:
    """RunReport mà `user` được thấy; không có hoặc không được thấy đều là 404 cùng một thông báo.

    Xét quyền bằng metadata trước, chỉ dựng RunReport đầy đủ khi đã được thấy."""
    report = get_run_report(store_path, run_id) if run_visible(user, store_path, run_id) else None
    if report is None:
        raise HTTPException(404, f"Không tìm thấy run {run_id}")
    return report
