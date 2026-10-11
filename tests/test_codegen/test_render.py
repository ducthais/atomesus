"""Render tất định từ Spec (P6): đủ 4 + N file, đúng cú pháp, Jinja của dbt đi qua nguyên vẹn."""

import ast
import json

import pytest
import yaml

from src.codegen.guard import expression_violations
from src.codegen.render import RenderError, check_syntax, dlt_name, file_hashes, render
from src.policy.loader import load_policy
from src.spec import SPEC_FIXTURES_DIR, Spec

SINGLE = "spec_01_pg_daily_orders.json"
MULTI = "spec_02_sales_multi_source.json"


def load(name: str, **changes) -> Spec:
    data = json.loads((SPEC_FIXTURES_DIR / name).read_text(encoding="utf-8"))
    for path, value in changes.items():
        target = data
        *parents, key = path.split("/")
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        target[key] = value
    return Spec.model_validate(data)


@pytest.fixture(scope="module")
def policy():
    return load_policy()


@pytest.mark.parametrize("name", [SINGLE, MULTI])
def test_renders_four_plus_n_files_with_valid_syntax(policy, name):
    spec = load(name)
    files = render(spec, policy)
    model = spec.transform.model_name
    expected = {
        f"ingestion/{model}_pipeline.py",
        f"dbt_project/models/gold/{model}.sql",
        f"dbt_project/models/gold/{model}.yml",
        f"infra/dagster/generated/{model}_asset.py",
    } | {f"dbt_project/models/staging/stg_{model}__{s.id}.sql" for s in spec.sources}
    assert set(files) == expected and len(files) == 4 + len(spec.sources)
    for path, content in files.items():
        if path.endswith(".py"):
            ast.parse(content)
        elif path.endswith(".yml"):
            yaml.safe_load(content)
        assert "[[" not in content and "[%" not in content, f"delimiter template còn sót trong {path}"


def test_render_is_deterministic(policy):
    first, second = render(load(MULTI), policy), render(load(MULTI), policy)
    assert first == second
    assert file_hashes(first) == file_hashes(second)
    hashes, files_hash = file_hashes(first)
    assert set(hashes) == set(first) and files_hash.startswith("sha256:")


@pytest.mark.parametrize("cron", ["0 2 * * *", "15 6 * * 1-5"])
def test_dagster_schedule_defaults_to_running(policy, cron):
    spec = load(SINGLE, **{"schedule/cron": cron})
    path = f"infra/dagster/generated/{spec.transform.model_name}_asset.py"
    dagster = render(spec, policy)[path]
    compile(dagster, path, "exec")
    imports = [node for node in ast.parse(dagster).body if isinstance(node, ast.ImportFrom)]
    assert any(
        node.module == "dagster" and any(alias.name == "DefaultScheduleStatus" for alias in node.names)
        for node in imports
    )
    assert "default_status=DefaultScheduleStatus.RUNNING" in dagster
    assert f"cron_schedule={json.dumps(spec.schedule.cron)}" in dagster
    assert 'execution_timezone="Asia/Ho_Chi_Minh"' in dagster


def test_single_source_pipeline_content(policy):
    files = render(load(SINGLE), policy)
    ingestion = files["ingestion/fct_daily_orders_pipeline.py"]
    assert 'dlt.secrets["sources.sales_pg.credentials"]' in ingestion
    assert 'schema="sales_raw"' in ingestion and 'table_name="raw_orders"' in ingestion
    assert 'write_disposition="replace"' in ingestion and "incremental" not in ingestion

    staging = files["dbt_project/models/staging/stg_fct_daily_orders__orders.sql"]
    assert "{{ source('bronze', 'raw_orders') }}" in staging
    assert "stg_fct_daily_orders__orders__rejected" in staging
    assert "try_strptime(cast(bronze.\"ordered_at_text\" as varchar), '%Y-%m-%d')" in staging
    assert "'reject_if:amount_vnd < 0'" in staging
    assert "order by _dedupe_order asc" in staging  # keep: first

    gold = files["dbt_project/models/gold/fct_daily_orders.sql"]
    assert "{{ ref('stg_fct_daily_orders__orders') }} as orders" in gold
    assert "and (status <> 'cancelled')" in gold and "group by 1" in gold

    schema = yaml.safe_load(files["dbt_project/models/gold/fct_daily_orders.yml"])
    gold_model = next(m for m in schema["models"] if m["name"] == "fct_daily_orders")
    tests = {c["name"]: c.get("data_tests", []) for c in gold_model["columns"]}
    assert tests == {"order_date": ["not_null", "unique"], "revenue": ["not_negative"], "order_count": []}

    dagster = files["infra/dagster/generated/fct_daily_orders_asset.py"]
    assert 'cron_schedule="0 2 * * *"' in dagster
    assert "from ingestion.fct_daily_orders_pipeline import pipeline, source" in dagster


def test_multi_source_pipeline_content(policy):
    files = render(load(MULTI), policy)
    ingestion = files["ingestion/fct_daily_revenue_pipeline.py"]
    assert "from dlt.sources.rest_api import rest_api_source" in ingestion
    assert 'incremental=dlt.sources.incremental("updated_at")' in ingestion
    assert '"data_selector": "data"' in ingestion and "'page_number'" in ingestion

    payments = files["dbt_project/models/staging/stg_fct_daily_revenue__payments.sql"]
    assert 'bronze."order_id" as _raw_2' in payments  # orderId đã được dlt chuẩn hóa thành order_id
    assert "when 'USD' then 25400" in payments  # tỷ giá đọc từ policy/domains.yml
    assert "'unknown_currency:amount_vnd'" in payments
    assert "'%d/%m/%Y'" in payments and "order by _dedupe_order desc" in payments  # keep: latest

    gold = files["dbt_project/models/gold/fct_daily_revenue.sql"]
    # payments là phía "nhiều" của ghép theo order_id: gộp trước theo khóa ghép để revenue không cộng trùng.
    assert (
        "left join (SELECT payments.order_id, SUM(payments.amount_vnd) AS __m1 "
        "FROM {{ ref('stg_fct_daily_revenue__payments') }} AS payments GROUP BY payments.order_id) as payments"
    ) in gold
    assert 'SUM(payments.__m1) as "revenue"' in gold and 'COUNT(DISTINCT orders.order_id) as "order_count"' in gold
    assert "on orders.order_id = payments.order_id" in gold
    assert "{% if is_incremental() %}" in gold and 'orders.order_date >= (select max("order_date")' in gold
    assert "unique_key=['order_date', 'channel']" in gold


def test_check_syntax_flags_broken_output():
    with pytest.raises(RenderError):
        check_syntax({"ingestion/x.py": "def broken(:\n"})
    with pytest.raises(RenderError):
        check_syntax({"dbt_project/models/gold/x.yml": "a: [1, 2\n"})


def test_dlt_name_matches_dlt_normalizer():
    names = ["orderId", "order_id", "paid_at", "ccy", "totalAmountVND", "HTTPCode"]
    expected = {"orderId": "order_id", "order_id": "order_id", "totalAmountVND": "total_amount_vnd"}
    for name, normalized in expected.items():
        assert dlt_name(name) == normalized
    snake_case = pytest.importorskip("dlt.common.normalizers.naming.snake_case")
    convention = snake_case.NamingConvention()
    assert [dlt_name(n) for n in names] == [convention.normalize_identifier(n) for n in names]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transform/filters", ["status <> '{{ env_var(\"OPENAI_API_KEY\") }}'"]),
        ("transform/filters", ["status <> 'x'; drop table orders"]),
        ("transform/filters", ["order_id in (select 1)"]),
        ("transform/filters", ["read_csv('/etc/passwd') is not null"]),
        ("transform/filters", ["status <> 'x' -- bỏ phần sau"]),
        ("sources/0/staging/reject_if", ["getenv('HOME') is null"]),
        # Bỏ qua tham số kiểu của CAST chỉ khi là chữ số đứng sau AS; mọi biến thể khác vẫn bị kiểm như hàm
        ("transform/filters", ["cast(amount as decimal(read_csv('x'))) > 0"]),
        ("transform/filters", ["decimal(18, 2) > 0"]),
        ("transform/filters", ["cast(amount as decimal(18,2)) > getenv('X')"]),
    ],
)
def test_guard_blocks_unsafe_expressions(field, value):
    assert expression_violations(load(SINGLE, **{field: value}))


def test_guard_allows_normal_expressions():
    assert expression_violations(load(SINGLE)) == []
    assert expression_violations(load(MULTI)) == []
    spec = load(
        SINGLE, **{"transform/filters": ["extract(year from order_date) = 2026", "status in ('paid', 'select')"]}
    )
    assert expression_violations(spec) == []
    # Ép kiểu có tham số (eval 2026-10-10: `SUM(CAST(orders.amount_text AS decimal(18,2)))` bị chặn nhầm)
    casts = load(
        SINGLE, **{"transform/filters": ["CAST(amount_text AS decimal(18,2)) > 0", "cast(status as varchar(20)) <> ''"]}
    )
    assert expression_violations(casts) == []


def test_post_run_fails_when_gold_is_empty_but_staging_is_not():
    from src.codegen.sandbox import _post_run
    from src.run_report import Check

    staging = Check(name="staging_reconcile", source_id="orders", result="pass", observed={"staging": 30})
    tests = Check(name="dbt_test", result="pass", observed={"passed": 4, "failed": 0})
    gate, errors = _post_run([staging, tests], {"gold_rows": 0, "gold_samples": []})
    assert gate.result == "fail" and gate.detail == "2/2 phép kiểm tra đạt; gold rỗng"
    assert errors == ["Model gold rỗng dù staging có 30 dòng: kiểm tra transform.filters và điều kiện join."]
    gate, errors = _post_run([staging, tests], {"gold_rows": 30, "gold_samples": []})
    assert gate.result == "pass" and errors == []
    failing = Check(name="dbt_test", result="fail", observed={"passed": 3, "failed": 1})
    gate, errors = _post_run([staging, failing], {"gold_rows": 30, "gold_samples": []})
    assert gate.result == "fail" and errors == ["Kiểm tra không đạt: dbt_test"]


def test_sources_extract_in_parallel_only_when_there_are_several(policy):
    multi = render(load(MULTI), policy)["ingestion/fct_daily_revenue_pipeline.py"]
    assert "resource_orders().parallelize()" in multi and "resource_payments().parallelize()" in multi
    single = next(v for k, v in render(load(SINGLE), policy).items() if k.startswith("ingestion/"))
    assert ".parallelize()" not in single
