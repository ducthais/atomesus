"""Node `planner` (P1, LLM rẻ): yêu cầu nghiệp vụ + danh mục nguồn → kế hoạch JSON (SPEC §14.2).

Kế hoạch chỉ được trỏ tới nguồn, bảng và cột có thật trong danh mục, kể cả trong `joins`. Trỏ sai thì gọi lại LLM
kèm lỗi cụ thể, tối đa `PLAN_ATTEMPTS` lần; vẫn sai → NEEDS_HUMAN. Cần bảng có thật nhưng nằm ngoài phạm vi người
dùng đã chọn thì dừng ngay và nói rõ cần chọn thêm bảng nào (LLM không tự mở rộng phạm vi).
Câu hỏi làm rõ (`question`) dừng run chờ người; luồng trả lời trong chat thuộc Sprint 2.
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.agents.llm import LLMOutputError, parse_json
from src.agents.nodes.common import AgentDeps, StopError, call_llm, llm_usage, prompt
from src.agents.state import AgentState
from src.pipeline.postgres import SourceUnavailableError

IDENTIFIER = r"^[a-z][a-z0-9_]{0,62}$"
PLAN_ATTEMPTS = 2  # lần đầu + 1 lần sửa khi kế hoạch trỏ sai bảng/cột


class _Plan(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PlanSource(_Plan):
    id: str = Field(pattern=IDENTIFIER)
    source_ref: str
    object: str
    purpose: str = ""


class PlanJoin(_Plan):
    left: str
    right: str
    type: Literal["left", "inner"] = "left"


class PlanMetric(_Plan):
    name: str
    definition: str = ""


class PlanConcept(_Plan):
    """Một khái niệm nghiệp vụ trong yêu cầu (khu vực, khách hàng, số tiền...) và cột thể hiện nó; null = không có."""

    term: str
    column: str | None = None


class Plan(_Plan):
    domain: str
    summary: str = ""
    model_name: str = Field(pattern=IDENTIFIER)
    sources: list[PlanSource] = Field(min_length=1)
    joins: list[PlanJoin] = Field(default_factory=list)
    grain: list[str] = Field(default_factory=list)
    metrics: list[PlanMetric] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    schedule: str = "0 2 * * *"
    question: str | None = None
    concepts: list[PlanConcept] = Field(default_factory=list)

    @field_validator("joins", "grain", "metrics", "filters", "concepts", mode="before")
    @classmethod
    def _null_is_empty(cls, value):
        return [] if value is None else value  # LLM hay trả `null` cho danh sách rỗng

    @field_validator("schedule", mode="before")
    @classmethod
    def _default_schedule(cls, value):
        return value or "0 2 * * *"


def _check_against_catalog(plan: Plan, catalog: dict) -> list[str]:
    domain = catalog.get(plan.domain)
    if domain is None:
        return [f"domain '{plan.domain}' không có trong danh mục"]
    errors = []
    if len({s.id for s in plan.sources}) != len(plan.sources):
        errors.append("sources[].id phải duy nhất")
    for source in plan.sources:
        entry = domain["sources"].get(source.source_ref)
        if entry is None:
            errors.append(f"nguồn '{source.source_ref}' không thuộc domain '{plan.domain}'")
        elif not entry["available"]:
            errors.append(f"nguồn '{source.source_ref}' ({entry['kind']}) {entry['reason']}")
        elif source.object not in entry["objects"]:
            errors.append(f"bảng '{source.object}' không có trong nguồn '{source.source_ref}'")
    return errors


def _columns(catalog: dict, domain: str, ref: str, table: str) -> list[str] | None:
    objects = catalog.get(domain, {}).get("sources", {}).get(ref, {}).get("objects", {})
    return objects.get(table) if isinstance(objects, dict) else None


def _complete_sources(plan: Plan, catalog: dict) -> Plan:
    """Bảng dùng trong joins, có thật và trong phạm vi đã chọn, mà LLM quên khai báo ở sources: thêm vào, tất định.

    Model rẻ hay chỉ ghi bảng chính vào sources dù đã ghép thêm bảng khác; gọi lại LLM để sửa việc này không ổn định.
    """
    known = {s.id for s in plan.sources} | {s.object for s in plan.sources}
    refs = catalog.get(plan.domain, {}).get("sources", {})
    added = []
    for join in plan.joins:
        for side in (join.left, join.right):
            alias = side.partition(".")[0]
            owners = [ref for ref in refs if _columns(catalog, plan.domain, ref, alias) is not None]
            if alias in known or len(owners) != 1 or not re.match(IDENTIFIER, alias):
                continue
            added.append(PlanSource(id=alias, source_ref=owners[0], object=alias, purpose="bảng dùng để ghép (joins)"))
            known.add(alias)
    return plan.model_copy(update={"sources": [*plan.sources, *added]}) if added else plan


def _check_joins(plan: Plan, catalog: dict, full: dict) -> tuple[list[str], list[str]]:
    """Mỗi vế `<id>.<cột>` của joins phải trỏ tới một nguồn trong `sources` và một cột có thật.

    Trả (lỗi LLM sửa được, bảng `nguồn.bảng` có thật nhưng ngoài phạm vi người dùng chọn).
    """
    by_alias = {s.id: s for s in plan.sources} | {s.object: s for s in plan.sources}
    refs = full.get(plan.domain, {}).get("sources", {})
    errors, outside = [], []
    for join in plan.joins:
        if join.left.partition(".")[0] == join.right.partition(".")[0]:
            errors.append(
                f"join [{join.left} = {join.right}] ghép một bảng với chính nó; mỗi join nối hai bảng khác nhau"
            )
            continue
        for side in (join.left, join.right):
            alias, dot, column = side.partition(".")
            if not dot or not column:
                errors.append(f"joins '{side}' phải có dạng <id>.<cột>")
                continue
            source = by_alias.get(alias)
            if source is not None:
                columns = _columns(catalog, plan.domain, source.source_ref, source.object)
                if columns is not None and column not in columns:
                    errors.append(f"joins '{side}': cột '{column}' không có trong bảng '{source.object}'")
                continue
            owner = next((ref for ref in refs if _columns(full, plan.domain, ref, alias) is not None), None)
            if owner is not None and _columns(catalog, plan.domain, owner, alias) is None:
                outside.append(f"{owner}.{alias}")
                continue
            holders = sorted(
                {t for ref in refs for t, cols in (refs[ref].get("objects") or {}).items() if alias in cols}
            )
            hint = f"; '{alias}' là cột của bảng {', '.join(holders)}" if holders else ""
            errors.append(
                f"joins '{side}' trỏ tới '{alias}' không phải một sources[].id: bảng dùng để ghép phải có trong "
                f"sources{hint}"
            )
    return errors + _unrelated_joins(plan, catalog), sorted(set(outside))


def _check_concepts(plan: Plan, catalog: dict) -> list[str]:
    """Cột LLM nói là thể hiện một khái niệm phải có thật trong một nguồn của kế hoạch."""
    by_alias = {s.id: s for s in plan.sources} | {s.object: s for s in plan.sources}
    errors = []
    for concept in plan.concepts:
        if not concept.column:
            continue
        alias, _, column = concept.column.partition(".")
        source = by_alias.get(alias)
        columns = _columns(catalog, plan.domain, source.source_ref, source.object) if source else None
        if source is None or (columns is not None and column not in columns):
            errors.append(
                f"concepts '{concept.term}': '{concept.column}' không phải <id>.<cột> có thật trong sources; "
                "để null nếu danh mục không có cột thể hiện khái niệm này"
            )
    return errors


def _unrelated_joins(plan: Plan, catalog: dict) -> list[str]:
    """Phép ghép giữa hai bảng cùng nguồn phải khớp một khóa ngoại (hoặc hai cột trùng tên).

    Cột có thật chưa đủ: `payment.customer_id = address.address_id` hợp lệ về tên nhưng ra số liệu sai.
    """
    by_alias = {s.id: s for s in plan.sources} | {s.object: s for s in plan.sources}
    errors = []
    for join in plan.joins:
        (la, _, lc), (ra, _, rc) = join.left.partition("."), join.right.partition(".")
        left, right = by_alias.get(la), by_alias.get(ra)
        if not left or not right or left.source_ref != right.source_ref or la == ra or lc == rc:
            continue
        relations = catalog.get(plan.domain, {}).get("sources", {}).get(left.source_ref, {}).get("relations")
        if relations is None:
            continue
        pair = {f"{left.object}.{lc}", f"{right.object}.{rc}"}
        if any(set(r.split(" -> ")) == pair for r in relations):
            continue
        between = [r for r in relations if {s.split(".")[0] for s in r.split(" -> ")} == {left.object, right.object}]
        hint = (
            f"quan hệ có thật giữa hai bảng: {'; '.join(between)}"
            if between
            else f"'{left.object}' và '{right.object}' không có khóa ngoại trực tiếp, ghép qua bảng trung gian theo relations"
        )
        errors.append(f"join [{join.left} = {join.right}] không khớp khóa ngoại nào; {hint}")
    return errors


def _narrow(catalog: dict, scope: list[dict] | None) -> tuple[dict, list[str]]:
    """Chỉ giữ nguồn và bảng người dùng đã chọn (`tables` rỗng = mọi bảng của nguồn).

    LLM chỉ thấy danh mục đã thu hẹp, và `_check_against_catalog` chạy trên chính danh mục này,
    nên kế hoạch trỏ ra ngoài lựa chọn của người dùng sẽ dừng ở NEEDS_HUMAN. Trả kèm các bảng đã chọn
    nhưng không có ở nguồn.
    """
    if not scope:
        return catalog, []
    wanted = {choice["source_ref"]: set(choice.get("tables") or []) for choice in scope}
    narrowed: dict = {}
    missing: list[str] = []
    for name, domain in catalog.items():
        sources = {}
        for ref, entry in domain["sources"].items():
            if ref not in wanted:
                continue
            tables, objects = wanted[ref], entry.get("objects", {})
            missing += [f"{ref}.{table}" for table in sorted(tables - set(objects))]
            kept = {t: cols for t, cols in objects.items() if not tables or t in tables}
            sources[ref] = {**entry, "objects": kept}
            if "relations" in entry:  # chỉ giữ quan hệ giữa các bảng còn lại
                sources[ref]["relations"] = [
                    r for r in entry["relations"] if all(side.split(".")[0] in kept for side in r.split(" -> "))
                ]
        if sources:
            narrowed[name] = {**domain, "sources": sources}
    missing += [ref for ref in wanted if not any(ref in d["sources"] for d in narrowed.values())]
    return narrowed, missing


def validate_plan_edit(raw: dict, deps: AgentDeps, scope: list[dict] | None) -> tuple[dict | None, list[str]]:
    """Kiểm tra kế hoạch do Engineer sửa bằng cùng hợp đồng và catalog thật như planner.

    Client chỉ là giao diện; nguồn, bảng, cột join và phạm vi đã chọn đều được xác minh lại ở backend.
    """
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        return None, [str(exc)]
    try:
        full = deps.catalog(deps.policy, deps.settings)
        catalog, missing = _narrow(full, scope)
    except SourceUnavailableError as exc:
        return None, [str(exc)]
    if missing:
        return None, ["Nguồn hoặc bảng đã chọn không có: " + ", ".join(missing)]
    plan = _complete_sources(plan, catalog)
    join_errors, outside = _check_joins(plan, catalog, full)
    errors = _check_against_catalog(plan, catalog) + join_errors + _check_concepts(plan, catalog)
    if outside:
        errors.append("Kế hoạch dùng bảng ngoài phạm vi đã chọn: " + ", ".join(outside))
    if plan.question:
        errors.append("Kế hoạch vẫn còn câu hỏi cần làm rõ: " + plan.question)
    return (plan.model_dump(), []) if not errors else (None, errors)


def planner(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        calls, usage = state.get("llm_calls", 0), list(state.get("llm_usage", []))
        try:
            full = deps.catalog(deps.policy, deps.settings)
            catalog, missing = _narrow(full, state.get("source_scope"))
            if missing:
                raise StopError("NEEDS_HUMAN", "plan", "Nguồn hoặc bảng đã chọn không có: " + ", ".join(missing))
            message: dict = {"yêu_cầu": state["user_request"], "danh_mục": catalog}
            for attempt in range(1, PLAN_ATTEMPTS + 1):
                text = call_llm(
                    {**state, "llm_calls": calls},
                    deps,
                    "plan",
                    "cheap",
                    prompt("planner"),
                    json.dumps(message, ensure_ascii=False),
                )
                calls += 1
                usage.append(llm_usage(text, "plan", "cheap", deps.policy))
                try:
                    plan = Plan.model_validate(parse_json(text))
                except (LLMOutputError, ValidationError) as exc:
                    if attempt == PLAN_ATTEMPTS:
                        raise StopError("NEEDS_HUMAN", "plan", f"Kế hoạch LLM trả về không hợp lệ: {exc}") from exc
                    message = {**message, "kế_hoạch_lần_trước": text[:4000], "lỗi_lần_trước": [str(exc)]}
                    continue
                plan = _complete_sources(plan, catalog)
                join_errors, outside = _check_joins(plan, catalog, full)
                errors = _check_against_catalog(plan, catalog) + join_errors + _check_concepts(plan, catalog)
                if outside:
                    raise StopError(
                        "NEEDS_HUMAN",
                        "plan",
                        "Yêu cầu cần thêm bảng ngoài phạm vi đã chọn: "
                        + ", ".join(outside)
                        + ". Chọn thêm các bảng này ở form Yêu cầu pipeline rồi tạo lại.",
                        need_tables=outside,
                    )
                if not errors:
                    break
                if attempt == PLAN_ATTEMPTS:
                    raise StopError(
                        "NEEDS_HUMAN", "plan", "Kế hoạch trỏ tới nguồn không đọc được: " + "; ".join(errors)
                    )
                message = {**message, "kế_hoạch_lần_trước": plan.model_dump(), "lỗi_lần_trước": errors}
        except SourceUnavailableError as exc:
            return StopError("INFRA_ERROR", "plan", str(exc)).update(state, llm_calls=calls, llm_usage=usage)
        except StopError as stop:
            return stop.update(state, llm_calls=calls, llm_usage=usage)
        unmapped = [c.term for c in plan.concepts if not c.column]
        if unmapped and not plan.question:  # LLM không được lặng lẽ thay khái niệm thiếu cột bằng phép tính khác
            plan = plan.model_copy(
                update={
                    "question": f"Các bảng đã chọn không có cột nào thể hiện: {', '.join(unmapped)}. "
                    "Cần thêm bảng nào (hoặc nói rõ dùng cột nào)?"
                }
            )
        update = {
            "plan": plan.model_dump(),
            "domain": plan.domain,
            "llm_calls": calls,
            "llm_usage": usage,
            "catalog": catalog,
        }
        if plan.question:
            return StopError("NEEDS_HUMAN", "plan", "Cần làm rõ yêu cầu", question=plan.question).update(
                state, **update
            )
        return {**update, "status": "GENERATING", "stage_reached": "plan"}

    return node
