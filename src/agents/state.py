"""State của graph sinh pipeline (SPEC §14.1). Mỗi node đọc state và trả về phần cập nhật."""

from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    version: int
    domain: str
    created_by: str  # từ phiên đăng nhập; "system" với run tự động
    trigger: str  # user_request | manual_optimize | regression
    parent_run_id: str | None  # run gốc khi Engineer "Sửa & chạy lại"; dùng để so sánh phiên bản
    regression: dict | None  # bằng chứng suy giảm, chỉ có khi trigger = regression (SPEC §14.1)
    user_request: str
    catalog: dict  # đối tượng nguồn đọc được của từng domain, đưa cho planner
    source_scope: list[dict] | None  # nguồn/bảng người dùng chọn [{source_ref, tables}]; None = mọi nguồn
    plan: dict | None
    schema_snapshot: dict  # từ resolve, theo sources[].id của plan; KHÔNG do LLM sinh
    spec: dict | None
    spec_history: list[dict]
    feedback: str | None  # lỗi cụ thể của lần sinh spec trước, đưa lại cho codegen
    generated_files: dict[str, str]
    files: dict[str, str]  # đường dẫn → hash
    files_hash: str | None
    gates: list[dict]
    checks: list[dict]
    attempts: dict[str, int]
    llm_calls: int
    llm_usage: list[dict]  # mỗi lần gọi LLM: {stage, tier, model, input_tokens, output_tokens, usd}
    cost: dict  # {estimated_bytes, actual_bytes, usd} từ cổng budget (P8)
    duration_sec: float
    error_trace: list[dict]
    timeline: list[dict]  # mốc thời gian {event, at, actor} để đo công sức (RunReport.timeline)
    stage_reached: str
    status: str
