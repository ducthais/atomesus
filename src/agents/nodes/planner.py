"""Node `planner` (P1, LLM rẻ): yêu cầu nghiệp vụ + danh mục nguồn → kế hoạch JSON (SPEC §14.2).

Kế hoạch chỉ được trỏ tới nguồn và bảng có thật trong danh mục; sai thì dừng ở NEEDS_HUMAN.
Câu hỏi làm rõ (`question`) dừng run chờ người; luồng trả lời trong chat thuộc Sprint 2.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agents.llm import LLMOutputError, parse_json
from src.agents.nodes.common import AgentDeps, StopError, call_llm, prompt
from src.agents.state import AgentState
from src.pipeline.postgres import SourceUnavailableError

IDENTIFIER = r"^[a-z][a-z0-9_]{0,62}$"


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
            sources[ref] = {**entry, "objects": {t: cols for t, cols in objects.items() if not tables or t in tables}}
        if sources:
            narrowed[name] = {**domain, "sources": sources}
    missing += [ref for ref in wanted if not any(ref in d["sources"] for d in narrowed.values())]
    return narrowed, missing


def planner(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        calls = state.get("llm_calls", 0)
        try:
            catalog, missing = _narrow(deps.catalog(deps.policy, deps.settings), state.get("source_scope"))
            if missing:
                raise StopError("NEEDS_HUMAN", "plan", "Nguồn hoặc bảng đã chọn không có: " + ", ".join(missing))
            user = json.dumps({"yêu_cầu": state["user_request"], "danh_mục": catalog}, ensure_ascii=False)
            text = call_llm(state, deps, "plan", "cheap", prompt("planner"), user)
            calls += 1
            try:
                plan = Plan.model_validate(parse_json(text))
            except (LLMOutputError, ValidationError) as exc:
                raise StopError("NEEDS_HUMAN", "plan", f"Kế hoạch LLM trả về không hợp lệ: {exc}") from exc
            errors = _check_against_catalog(plan, catalog)
            if errors:
                raise StopError("NEEDS_HUMAN", "plan", "Kế hoạch trỏ tới nguồn không đọc được: " + "; ".join(errors))
        except SourceUnavailableError as exc:
            return StopError("INFRA_ERROR", "plan", str(exc)).update(state, llm_calls=calls)
        except StopError as stop:
            return stop.update(state, llm_calls=calls)
        update = {"plan": plan.model_dump(), "domain": plan.domain, "llm_calls": calls, "catalog": catalog}
        if plan.question:
            return StopError("NEEDS_HUMAN", "plan", "Cần làm rõ yêu cầu", question=plan.question).update(
                state, **update
            )
        return {**update, "status": "GENERATING", "stage_reached": "plan"}

    return node
