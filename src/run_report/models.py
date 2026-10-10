"""RunReport — hợp đồng dữ liệu bằng chứng của một run (SPEC §8.2).

Đây là ranh giới giữa các module: sửa model này phải báo cả nhóm trong ngày (SPEC §8).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

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

GateName = Literal[
    "spec_schema",
    "schema_real",
    "currency",
    "join_fanout",
    "time_grain",
    "filter_values",
    "semantic",
    "ast_guard",
    "budget",
    "arbitrate",
    "post_run",
]
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
# Trường tiến độ của Deployment do webhook GitHub / deployer ghi sau khi mở pull request
DEPLOYMENT_PROGRESS: frozenset[str] = frozenset(
    {
        "ci_conclusion",
        "ci_url",
        "ci_at",
        "merged_at",
        "merge_commit_sha",
        "closed_at",
        "deployed_commit_sha",
        "deployed_at",
    }
)


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
    """`usd`: chi phí quét kho dữ liệu (ước tính ở cổng budget). `llm_usd`: tổng tiền gọi LLM của run (`llm_usage`)."""

    estimated_bytes: int = Field(ge=0)
    actual_bytes: int = Field(ge=0)
    usd: float = Field(ge=0)
    llm_usd: float = Field(default=0, ge=0)


class LlmCall(_Contract):
    """Một lần gọi LLM. Token do nhà cung cấp báo; `usd` tính theo `budgets.yml: pricing.llm`, None nếu chưa có giá."""

    stage: str
    tier: str
    model: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usd: float | None = Field(default=None, ge=0)


TimelineEventName = Literal[
    "submitted",
    "plan_ready",
    "plan_edited",
    "plan_confirmed",
    "spec_ready",
    "mapping_submitted",
    "changes_requested",
    "revised",
    "cancelled",
    "finished",
]


class TimelineEvent(_Contract):
    """Mốc thời gian của một run, để đo công sức: máy chạy bao lâu, người (chỉnh mapping, duyệt) mất bao lâu.

    submitted: Engineer gửi yêu cầu. plan_ready: agent lập xong kế hoạch, chờ xác nhận (P2). plan_confirmed: Engineer
    xác nhận kế hoạch. spec_ready: agent viết xong Spec đã qua cổng. mapping_submitted: Engineer
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


class Deployment(_Contract):
    """Pull request P16 mở vào repo pipeline (SPEC §9.5). `status` vẫn APPROVED trong lúc chờ merge.

    `state`: open khi vừa mở; webhook GitHub cập nhật merged / closed. `ci_conclusion`: kết luận CI gần nhất của
    `head_sha` (success, failure, cancelled, timed_out). `deployed_*`: deployer báo đã cài lên production.
    Các trường sau `opened_at` đều tùy chọn để bản ghi cũ vẫn đọc được, và chỉ có mặt khi đã có giá trị để bản xuất ra
    (JSON lưu trữ, API) giữ đúng dạng cũ cho tới khi webhook / deployer ghi.
    """

    repo: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    pr_number: int = Field(ge=1)
    pr_url: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    state: Literal["open", "merged", "closed"] = "open"
    opened_at: datetime
    ci_conclusion: str | None = None
    ci_url: str | None = None
    ci_at: datetime | None = None
    merged_at: datetime | None = None
    merge_commit_sha: str | None = None
    closed_at: datetime | None = None
    deployed_commit_sha: str | None = None
    deployed_at: datetime | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_progress(self, handler):
        data = handler(self)
        return {key: value for key, value in data.items() if value is not None or key not in DEPLOYMENT_PROGRESS}


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
    llm_usage: list[LlmCall] = Field(default_factory=list)
    error_trace: list[dict[str, Any]] = Field(default_factory=list)
    approval: Approval | None = None
    review_history: list[Approval] = Field(
        default_factory=list,
        description="Các quyết định review của version cũ; approval là quyết định của version hiện tại.",
    )
    timeline: list[TimelineEvent] = Field(default_factory=list)
    deployment: Deployment | None = Field(
        default=None, description="Pull request P16 của bản đã duyệt; null với run chưa deploy hoặc dữ liệu cũ."
    )

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
