"""RunReport — hợp đồng dữ liệu bằng chứng của một run (SPEC §8.2).

Đây là ranh giới giữa các module: sửa model này phải báo cả nhóm trong ngày (SPEC §8).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Trigger = Literal["user_request", "manual_optimize", "regression"]

RunStatus = Literal[
    "DRAFT",
    "PLAN_CONFIRMED",
    "GENERATING",
    "VALIDATING",
    "READY_FOR_REVIEW",
    "APPROVED",
    "DEPLOYED",
    "NEEDS_HUMAN",
    "BLOCKED_SEMANTIC_CONFLICT",
    "BLOCKED_BUDGET",
    "BLOCKED_AST",
    "REJECTED",
    "INFRA_ERROR",
    "INTERNAL_ERROR",
    "DEPLOY_FAILED",
    "CANCELLED",
    "NO_IMPROVEMENT",
]

# P0–P17, trùng với SSE `stage` (SPEC §8.3, §9)
Stage = Literal[
    "intake",
    "plan",
    "confirm",
    "resolve",
    "propose",
    "arbitrate",
    "render",
    "static",
    "budget",
    "el_run",
    "el_validate",
    "t_run",
    "t_validate",
    "optimize",
    "report",
    "review",
    "deploy",
    "persist",
]

GateName = Literal["spec_schema", "schema_real", "time_grain", "semantic", "ast_guard", "budget", "post_run"]
CheckName = Literal[
    "row_reconcile",
    "hash_reconcile",
    "idempotent",
    "staging_reconcile",
    "dbt_test",
    "metric_reconcile",
]
Result = Literal["pass", "fail"]
Decision = Literal["approved", "rejected", "changes_requested"]

# Kiểm tra theo từng nguồn phải có source_id; kiểm tra của model thì không (SPEC §8.2)
PER_SOURCE_CHECKS: frozenset[str] = frozenset({"row_reconcile", "hash_reconcile", "idempotent", "staging_reconcile"})
# Trạng thái chỉ đạt được sau khi Reviewer duyệt (SPEC §11.1)
APPROVED_STATUSES: frozenset[str] = frozenset({"APPROVED", "DEPLOYED", "DEPLOY_FAILED"})


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GateResult(_Contract):
    name: GateName
    result: Result
    detail: str = ""
    observed: dict[str, Any] = Field(default_factory=dict)


class Check(_Contract):
    name: CheckName
    source_id: str | None = None
    result: Result
    observed: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _source_id_matches_scope(self) -> "Check":
        if self.name in PER_SOURCE_CHECKS and not self.source_id:
            raise ValueError(f"check '{self.name}' chạy theo từng nguồn, bắt buộc có source_id")
        if self.name not in PER_SOURCE_CHECKS and self.source_id:
            raise ValueError(f"check '{self.name}' là của model, không được có source_id")
        return self


class Cost(_Contract):
    estimated_bytes: int = Field(ge=0)
    actual_bytes: int = Field(ge=0)
    usd: float = Field(ge=0)


TimelineEventName = Literal["submitted", "spec_ready", "mapping_submitted", "finished"]


class TimelineEvent(_Contract):
    """Mốc thời gian của một run, để đo công sức: máy chạy bao lâu, người (chỉnh mapping, duyệt) mất bao lâu.

    submitted: Engineer gửi yêu cầu. spec_ready: agent viết xong Spec đã qua cổng. mapping_submitted: Engineer
    duyệt / sửa mapping xong và cho chạy thử. finished: agent dừng (chạy thử xong hoặc bị chặn). Thời điểm duyệt
    lấy từ approval.at.
    """

    event: TimelineEventName
    at: datetime
    actor: str | None = None


class Timing(_Contract):
    baseline_sec: float | None = Field(default=None, ge=0)
    optimized_sec: float | None = Field(default=None, ge=0)


class OptimizerStep(_Contract):
    rule: str
    applied: bool
    before_sec: float | None = Field(default=None, ge=0)
    after_sec: float | None = Field(default=None, ge=0)
    reason: str | None = None


class Attempts(_Contract):
    spec: int = Field(default=0, ge=0)
    sql: int = Field(default=0, ge=0)


class Approval(_Contract):
    """Chữ ký phê duyệt (bất biến 3, SPEC §19). Gắn với đúng files_hash và version."""

    decision: Decision
    reviewer: str = Field(min_length=1)
    files_hash: str
    version: int = Field(ge=1)
    at: datetime
    comment: str = ""


class RunReport(_Contract):
    run_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    domain: str = Field(min_length=1)
    created_by: str = Field(
        min_length=1, description="Engineer tạo run, lấy từ phiên đăng nhập; 'system' với run tự động"
    )
    trigger: Trigger
    parent_run_id: str | None = None
    source_scope: list[dict[str, Any]] | None = Field(
        default=None, description="Nguồn và bảng người dùng chọn khi tạo run; null = để agent chọn trong mọi nguồn"
    )
    regression: dict[str, Any] | None = None
    created_at: datetime
    user_request: str
    status: RunStatus
    stage_reached: Stage
    plan: dict[str, Any] | None = None
    spec: dict[str, Any] | None = None
    spec_history: list[dict[str, Any]] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    files_hash: str | None = None
    gates: list[GateResult] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    cost: Cost
    timing: Timing = Field(default_factory=Timing)
    optimizer: list[OptimizerStep] = Field(default_factory=list)
    attempts: Attempts = Field(default_factory=Attempts)
    llm_calls: int = Field(ge=0)
    error_trace: list[dict[str, Any]] = Field(default_factory=list)
    approval: Approval | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _approval_is_valid_signature(self) -> "RunReport":
        if self.status in APPROVED_STATUSES and (self.approval is None or self.approval.decision != "approved"):
            raise ValueError(f"status {self.status} bắt buộc có approval.decision = 'approved'")
        if self.approval is None:
            return self
        if self.approval.reviewer == self.created_by:
            raise ValueError("engineer không được tự duyệt run của mình (approval.reviewer == created_by)")
        if self.approval.files_hash != self.files_hash:
            raise ValueError("approval.files_hash không khớp files_hash hiện tại")
        if self.approval.version != self.version:
            raise ValueError("approval.version không khớp version hiện tại")
        return self
