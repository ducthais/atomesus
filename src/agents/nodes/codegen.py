"""Node `codegen` (P3 resolve + P4 propose, LLM mạnh): kế hoạch + schema thật + dòng mẫu → Spec (SPEC §14.2).

Spec được kiểm tra ngay (`src/agents/validate.py`). Sai `spec_schema`/`schema_real` thì gọi lại LLM kèm
lỗi cụ thể, tối đa `budgets.yml: per_run.max_spec_attempts` lần (vòng spec, SPEC §11.3); hết lượt → NEEDS_HUMAN.
Biểu thức nguy hiểm → BLOCKED_AST, không thử lại.
"""

import json

from src.agents.llm import LLMOutputError, parse_json
from src.agents.nodes.common import AgentDeps, StopError, call_llm, llm_usage, prompt
from src.agents.resolve import ResolveError
from src.agents.state import AgentState
from src.agents.validate import MONEY, NUMERIC_TYPES, currency_columns, requested_period, validate_spec
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
    - Khóa ghép của nguồn file / API để varchar trong khi phía kia là số → ép theo kiểu phía kia (`_align_join_types`).
    - `unique_key` / `partition_by` / `tests[].column` có tiền tố `<id>.` → bỏ tiền tố (`_bare_gold_columns`).

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
    _align_join_types(transform, sources, snapshot)
    _bare_gold_columns(transform)
    return raw


def _align_join_types(transform: dict, sources: dict, snapshot: dict) -> None:
    """Khóa ghép lệch kiểu sau staging: cột của nguồn file / API (đọc vào là chuỗi) ép theo kiểu phía bên kia.

    CSV đọc mọi cột thành chuỗi, LLM hay để `order_id` của file là varchar trong khi bảng Postgres là integer, và lặp lại
    cả khi được báo (eval 2026-10-08). Staging ép bằng TRY_CAST nên giá trị không ép được bị tách sang bảng rejected.
    """

    def column(ref: object) -> tuple[str, dict | None]:
        sid, _, name = str(ref).partition(".")
        staging = (sources.get(sid) or {}).get("staging")
        columns = staging.get("columns") if isinstance(staging, dict) else None
        return sid, next((c for c in columns or [] if isinstance(c, dict) and c.get("as") == name), None)

    for join in transform.get("joins") or []:
        for pair in join.get("on") or [] if isinstance(join, dict) else []:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            (left_id, left), (right_id, right) = column(pair[0]), column(pair[1])
            if not left or not right or left.get("type") == right.get("type"):
                continue
            for sid, mine, other in ((left_id, left, right), (right_id, right, left)):
                if (snapshot.get(sid) or {}).get("kind") in ("filesystem", "rest_api") and mine.get(
                    "type"
                ) == "varchar":
                    mine["type"] = other.get("type")
                    break


def _bare_gold_columns(transform: dict) -> None:
    """`unique_key` / `partition_by` / `tests[].column` là cột của bảng gold (không có tiền tố nguồn): bỏ `<id>.` thừa.

    LLM hay chép nguyên phần tử grain (`customers.customer_id`) vào các trường này và lặp lại cả khi được báo.
    """
    metrics = [m.get("name") for m in transform.get("metrics") or [] if isinstance(m, dict)]
    gold = {str(g).rpartition(".")[2] for g in transform.get("grain") or []} | set(metrics)

    def bare(value: object) -> object:
        name = value.rpartition(".")[2] if isinstance(value, str) else None
        return name if name in gold else value

    if isinstance(transform.get("unique_key"), list):
        transform["unique_key"] = [bare(key) for key in transform["unique_key"]]
    if transform.get("partition_by"):
        transform["partition_by"] = bare(transform["partition_by"])
    for test in transform.get("tests") or []:
        if isinstance(test, dict) and "column" in test:
            test["column"] = bare(test["column"])


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
    if state.get("regression"):  # run sửa của Day 2: viết lại Spec đang chạy, không viết từ đầu
        message["sửa_suy_giảm"] = {
            "yêu_cầu": (
                "Pipeline đang chạy theo `spec_đang_chạy` bị suy giảm như `bằng_chứng`. Trả về Spec đầy đủ đã sửa: giữ "
                "nguyên ý nghĩa đầu ra (cùng model_name, grain, chỉ số, bộ lọc, nguồn), chỉ đổi phần làm pipeline "
                "chậm / tốn / sai test, ví dụ nạp tăng dần theo cursor_field, bỏ cột không dùng, sửa kiểu và định dạng."
            ),
            "bằng_chứng": state["regression"].get("rules", []),
            "spec_đang_chạy": state["regression"].get("current_spec"),
        }
    if revision := state.get("revision"):  # người yêu cầu thay đổi (Reviewer "Yêu cầu sửa" hoặc Engineer qua chat)
        message["yêu_cầu_thay_đổi"] = (
            f"{revision.get('by')} yêu cầu thay đổi pipeline đang có (`spec_hiện_tại`): «{revision.get('comment')}». "
            "Trả về Spec đầy đủ đã áp dụng ĐÚNG yêu cầu này. Yêu cầu thay đổi được ưu tiên hơn `kế_hoạch` ban đầu: "
            "được thêm, bỏ hoặc đổi chỉ số, độ hạt, bộ lọc, cột, miễn là chỉ dùng nguồn và cột có trong schema_snapshot. "
            "Giữ nguyên mọi phần yêu cầu không nhắc tới."
        )
        message["spec_hiện_tại"] = revision.get("spec") or state.get("spec")
    if state.get("feedback"):
        if state.get("repair") and state.get("stage_reached") == "propose":  # vừa từ node repair: lỗi khi chạy thử
            message["yêu_cầu_sửa"] = REPAIR_PROMPTS[state["repair"]]
            message["lỗi_chạy_thử"] = state["feedback"]
        else:
            message["yêu_cầu_sửa"] = (
                "Spec lần trước bị từ chối vì các lỗi trong `lỗi_lần_trước`. Trả về Spec đầy đủ đã sửa TẤT CẢ các lỗi "
                "đó, giữ nguyên phần đúng. Không lặp lại giá trị đã bị báo sai."
            )
            message["lỗi_lần_trước"] = state["feedback"]
        message["spec_lần_trước"] = state.get("spec")
    return json.dumps(message, ensure_ascii=False)


REPAIR_PROMPTS = {
    "propose": (
        "Spec lần trước đã qua kiểm tra tĩnh nhưng CHẠY THỬ trên dữ liệu thật không đạt, chi tiết trong `lỗi_chạy_thử` "
        "(kèm phase, nguồn, số đo và dòng mẫu). Trả về Spec đầy đủ đã sửa đúng nguyên nhân đó, giữ nguyên ý nghĩa đầu "
        "ra (model_name, grain, chỉ số) trừ khi lỗi nằm chính ở đó."
    ),
    "sql_fix": (
        "Chế độ sql_fix: chạy thử không đạt vì biểu thức SQL, chi tiết trong `lỗi_chạy_thử`. CHỈ sửa biểu thức SQL: "
        "transform.metrics[].expr, transform.filters, sources[].staging.reject_if. Trả về Spec đầy đủ; mọi phần khác "
        "giữ nguyên như `spec_lần_trước` (phần khác có đổi cũng bị bỏ qua)."
    ),
}


def _sql_only(previous: dict, proposed: object) -> object:
    """Chế độ sql_fix: lấy Spec cũ, chỉ nhận biểu thức SQL mới (metrics[].expr theo tên, filters, reject_if theo nguồn).

    Bảo đảm vòng SQL không âm thầm đổi mapping, khóa hay nguồn mà người đã thấy ở bản trước.
    """
    if not isinstance(proposed, dict) or not isinstance(proposed.get("transform"), dict):
        return proposed
    merged = json.loads(json.dumps(previous))
    new_exprs = {
        m.get("name"): m.get("expr") for m in proposed["transform"].get("metrics") or [] if isinstance(m, dict)
    }
    for metric in merged["transform"].get("metrics") or []:
        if new_exprs.get(metric.get("name")):
            metric["expr"] = new_exprs[metric["name"]]
    if isinstance(proposed["transform"].get("filters"), list):
        merged["transform"]["filters"] = proposed["transform"]["filters"]
    new_rejects = {
        s.get("id"): (s.get("staging") or {}).get("reject_if")
        for s in proposed.get("sources") or []
        if isinstance(s, dict) and isinstance(s.get("staging"), dict)
    }
    for source in merged.get("sources") or []:
        if isinstance(new_rejects.get(source.get("id")), list):
            source["staging"]["reject_if"] = new_rejects[source["id"]]
    return merged


def _request(state: AgentState) -> str:
    """Yêu cầu dùng cho cổng `time_grain`: yêu cầu thay đổi nói kỳ gộp mới ("đổi sang theo tháng") thì theo nó."""
    comment = (state.get("revision") or {}).get("comment") or ""
    return comment if requested_period(comment) else state["user_request"]


def codegen(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        calls, usage = state.get("llm_calls", 0), list(state.get("llm_usage", []))
        attempts = dict(state.get("attempts") or {"spec": 0, "sql": 0})
        snapshot = state.get("schema_snapshot")
        try:
            if not snapshot:
                snapshot = deps.snapshot(state["plan"], deps.policy, deps.settings)
            text = call_llm(state, deps, "propose", "strong", _system_prompt(), _user_message(state, deps, snapshot))
            calls += 1
            usage.append(llm_usage(text, "propose", "strong", deps.policy))
        except ResolveError as exc:
            return StopError("NEEDS_HUMAN", "resolve", str(exc)).update(state)
        except SourceUnavailableError as exc:
            return StopError("INFRA_ERROR", "resolve", str(exc)).update(state)
        except StopError as stop:
            return stop.update(state, llm_calls=calls, llm_usage=usage, schema_snapshot=snapshot)

        try:
            raw = parse_json(text)
            if state.get("repair") == "sql_fix" and isinstance(state.get("spec"), dict):
                raw = _sql_only(state["spec"], raw)
            raw = _add_key_columns(raw, snapshot, deps.policy)
            result = validate_spec(raw, snapshot, deps.policy, _request(state))
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
            "llm_usage": usage,
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
