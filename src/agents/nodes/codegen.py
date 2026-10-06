"""Node `codegen` (P3 resolve + P4 propose, LLM mạnh): kế hoạch + schema thật + dòng mẫu → Spec (SPEC §14.2).

Spec được kiểm tra ngay (`src/agents/validate.py`). Sai `spec_schema`/`schema_real` thì gọi lại LLM kèm
lỗi cụ thể, tối đa `budgets.yml: per_run.max_spec_attempts` lần (vòng spec, SPEC §11.3); hết lượt → NEEDS_HUMAN.
Biểu thức nguy hiểm → BLOCKED_AST, không thử lại.
"""

import json

from src.agents.llm import LLMOutputError, parse_json
from src.agents.nodes.common import AgentDeps, StopError, call_llm, prompt
from src.agents.resolve import ResolveError
from src.agents.state import AgentState
from src.agents.validate import MONEY, NUMERIC_TYPES, currency_columns, validate_spec
from src.pipeline.postgres import SourceUnavailableError
from src.spec import SPEC_FIXTURES_DIR

EXAMPLE_SPEC = SPEC_FIXTURES_DIR / "spec_02_sales_multi_source.json"
TEMPORAL = {"date", "timestamp"}

# Kiểu Postgres (information_schema.data_type) → kiểu staging cho phép (src/spec/models.py COLUMN_TYPE).
PG_TYPES = {
    "smallint": "integer",
    "integer": "integer",
    "bigint": "bigint",
    "numeric": "decimal(18,2)",
    "money": "decimal(18,2)",
    "real": "double",
    "double precision": "double",
    "text": "varchar",
    "character varying": "varchar",
    "character": "varchar",
    "uuid": "varchar",
    "boolean": "boolean",
    "date": "date",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamp",
}


def _fix_currency(sources: dict, snapshot: dict, policy, domain_name: object) -> None:
    """Cột tiền của nguồn có cột mã tiền tệ (thấy trong dòng mẫu) phải quy đổi; `currency` trỏ tới cột không có thì bỏ.

    LLM hay gắn nhầm `currency` sang nguồn khác có cột cùng tên `amount_vnd`, rồi khi bị báo thì bỏ luôn cả hai
    (run c6a290d8). Mã tiền tệ và cột mã đọc từ dữ liệu thật nên sửa tất định được.
    """
    domains = getattr(getattr(policy, "domains", None), "domains", {})
    for sid, source in sources.items():
        found = snapshot.get(sid) or {}
        staging = source.get("staging")
        if not found or not isinstance(staging, dict) or not isinstance(staging.get("columns"), list):
            continue
        domain = domains.get(domain_name) if isinstance(domain_name, str) else None
        rates = domain.currency if domain else None
        codes = currency_columns(found, {c.upper() for c in rates.rates}) if rates else {}
        for column in staging["columns"]:
            if not isinstance(column, dict):
                continue
            current = column.get("currency")
            if isinstance(current, dict) and current.get("code_column") not in (found.get("columns") or {}):
                column["currency"] = current = None
            money = MONEY.search(str(column.get("from", ""))) or MONEY.search(str(column.get("as", "")))
            numeric = str(column.get("type", "")).startswith(NUMERIC_TYPES)
            if codes and not current and money and numeric and column.get("from") not in codes:
                column["currency"] = {"code_column": next(iter(codes)), "to": rates.base}


def _add_key_columns(raw: object, snapshot: dict, policy=None) -> object:
    """Sửa tất định hai lỗi LLM lặp lại cả khi được báo, trước khi kiểm tra Spec.

    - Kiểu Postgres chép nguyên vào staging (`smallint`, `text`, `numeric`) → kiểu staging tương ứng.
    - Cột dùng để ghép hoặc gộp (`<id>.<cột>` trong joins/grain) mà LLM quên khai báo ở staging thì thêm vào.
    - Nguồn rest_api: `endpoint`, `records_path`, `pagination` chép từ schema_snapshot (đọc từ OpenAPI).
    - Nguồn filesystem: `path_pattern`, `format` chép từ schema_snapshot; cursor chỉ có thể là `modification_date`.
    - Tiền tệ (có `policy`): cột tiền của nguồn có cột mã tiền tệ được gắn `currency`; `currency` sai cột thì bỏ.
    - `time_grain.column` viết thiếu / thừa tiền tố `<id>.` so với phần tử tương ứng của grain thì lấy đúng phần tử đó.
    - `date_formats` trên cột thô đã là ngày/giờ (không phải chuỗi) thì bỏ: staging ép thẳng kiểu. LLM hay thêm vì dòng
      mẫu hiện giá trị dạng chuỗi ISO, nhưng ở bronze cột đã là timestamp nên mọi dòng sẽ trượt định dạng và bị tách.

    LLM hay bỏ khóa ghép (ví dụ `address.city_id`) khỏi staging.columns và lặp lại lỗi đó cả khi được báo. Chỉ thêm cột
    thô có thật trong schema_snapshot, chưa được khai báo dưới tên nào, giữ nguyên tên và ép kiểu theo kiểu nguồn.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("transform"), dict):
        return raw
    transform = raw["transform"]
    for source in raw.get("sources") or []:  # trường riêng của API / file lấy từ nguồn thật, không để LLM đoán
        found = snapshot.get(source.get("id")) if isinstance(source, dict) else None
        if found and found.get("kind") == "rest_api":
            source.pop("table", None)
            source.update(
                kind="rest_api",
                endpoint=found["endpoint"],
                records_path=found["records_path"],
                pagination=found["pagination"],
            )
        elif found and found.get("kind") == "filesystem":
            source.pop("table", None)
            source.update(kind="filesystem", path_pattern=found["path_pattern"], format=found["format"])
            if source.get("cursor_field") not in (None, "modification_date"):
                source["cursor_field"] = None  # file chỉ tăng dần theo thời điểm sửa file
    grain = [g for g in transform.get("grain") or [] if isinstance(g, str)]
    time_grain = transform.get("time_grain")
    if isinstance(time_grain, dict) and time_grain.get("column") not in grain:
        name = str(time_grain.get("column")).rpartition(".")[2]
        same = [g for g in grain if g.rpartition(".")[2] == name]
        if len(same) == 1:
            time_grain["column"] = same[0]
    refs = [side for j in transform.get("joins") or [] for pair in j.get("on") or [] for side in pair]
    refs += [g for g in transform.get("grain") or [] if isinstance(g, str)]
    sources = {s.get("id"): s for s in raw.get("sources") or [] if isinstance(s, dict)}
    for sid, source in sources.items():  # LLM hay chép nguyên kiểu Postgres (smallint, text, numeric) vào staging
        staging = source.get("staging")
        raw_columns = (snapshot.get(sid) or {}).get("columns") or {}
        for column in staging.get("columns") or [] if isinstance(staging, dict) else []:
            if not isinstance(column, dict):
                continue
            if str(column.get("type", "")).lower() in PG_TYPES:
                column["type"] = PG_TYPES[str(column["type"]).lower()]
            if column.get("date_formats") and PG_TYPES.get(raw_columns.get(column.get("from"), "")) in TEMPORAL:
                column["date_formats"] = None
    if policy is not None:
        _fix_currency(sources, snapshot, policy, raw.get("domain"))
    for ref in refs:
        source_id, dot, column = str(ref).partition(".")
        source, raw_columns = sources.get(source_id), (snapshot.get(source_id) or {}).get("columns") or {}
        staging = source.get("staging") if source else None
        if not dot or not isinstance(staging, dict) or not isinstance(staging.get("columns"), list):
            continue
        declared = {c.get(k) for c in staging["columns"] if isinstance(c, dict) for k in ("from", "as")}
        kind = PG_TYPES.get(raw_columns.get(column, ""))
        if column not in declared and kind:
            staging["columns"].append({"from": column, "as": column, "type": kind})
    return raw


def _system_prompt() -> str:
    return prompt("codegen").replace("{example}", EXAMPLE_SPEC.read_text(encoding="utf-8"))


def _user_message(state: AgentState, deps: AgentDeps, snapshot: dict) -> str:
    domain = deps.policy.domains.domains[state["plan"]["domain"]]
    message = {
        "yêu_cầu": state["user_request"],
        "kế_hoạch": state["plan"],
        "schema_snapshot": snapshot,
        "chính_sách": {
            "domain": state["plan"]["domain"],
            "tiền_gốc": domain.currency.base,
            "tỷ_giá": domain.currency.rates,
            "nguồn_được_phép": {ref: decl.kind for ref, decl in domain.sources.items()},
        },
    }
    if state.get("feedback"):
        message["yêu_cầu_sửa"] = (
            "Spec lần trước bị từ chối vì các lỗi trong `lỗi_lần_trước`. Trả về Spec đầy đủ đã sửa TẤT CẢ các lỗi đó, "
            "giữ nguyên phần đúng. Không lặp lại giá trị đã bị báo sai."
        )
        message["lỗi_lần_trước"] = state["feedback"]
        message["spec_lần_trước"] = state.get("spec")
    return json.dumps(message, ensure_ascii=False)


def codegen(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        calls = state.get("llm_calls", 0)
        attempts = dict(state.get("attempts") or {"spec": 0, "sql": 0})
        snapshot = state.get("schema_snapshot")
        try:
            if not snapshot:
                snapshot = deps.snapshot(state["plan"], deps.policy, deps.settings)
            text = call_llm(state, deps, "propose", "strong", _system_prompt(), _user_message(state, deps, snapshot))
            calls += 1
        except ResolveError as exc:
            return StopError("NEEDS_HUMAN", "resolve", str(exc)).update(state)
        except SourceUnavailableError as exc:
            return StopError("INFRA_ERROR", "resolve", str(exc)).update(state)
        except StopError as stop:
            return stop.update(state, llm_calls=calls, schema_snapshot=snapshot)

        try:
            raw = _add_key_columns(parse_json(text), snapshot, deps.policy)
            result = validate_spec(raw, snapshot, deps.policy, state["user_request"])
            errors, gates = result.errors, [gate.model_dump() for gate in result.gates]
        except LLMOutputError as exc:
            raw, result, errors, gates = None, None, [str(exc)], []
        history = [
            *state.get("spec_history", []),
            {"attempt": len(state.get("spec_history", [])) + 1, "spec": raw, "errors": errors},
        ]
        kept = [g for g in state.get("gates", []) if g["name"] == "semantic"]  # cổng P3 đã chạy trước codegen
        update = {
            "schema_snapshot": snapshot,
            "llm_calls": calls,
            "spec_history": history,
            "gates": [*kept, *gates],
            "spec": raw,
        }

        if result is not None and result.blocked:
            return StopError("BLOCKED_AST", "static", "; ".join(errors)).update(state, **update)
        if errors:
            attempts["spec"] += 1
            limit = deps.policy.budgets.per_run.max_spec_attempts
            if attempts["spec"] > limit:
                message = f"Spec vẫn sai sau {limit} lần sửa (budgets.yml max_spec_attempts): " + "; ".join(errors)
                return StopError("NEEDS_HUMAN", "static", message).update(state, attempts=attempts, **update)
            return {**update, "attempts": attempts, "feedback": errors, "stage_reached": "static"}
        spec = result.spec.model_dump(by_alias=True, mode="json")
        return {
            **update,
            "spec": spec,
            "feedback": None,
            "attempts": attempts,
            "status": "VALIDATING",
            "stage_reached": "static",
        }

    return node
