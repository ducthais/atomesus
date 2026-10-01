import json
import re

import pytest
from pydantic import ValidationError

from src.spec import SCHEMA_PATH, SPEC_FIXTURES_DIR, Spec

SINGLE = "spec_01_pg_daily_orders.json"
MULTI = "spec_02_sales_multi_source.json"


def _load(name: str) -> dict:
    return json.loads((SPEC_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _invalid(data: dict, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Spec.model_validate(data)


@pytest.mark.parametrize("path", sorted(SPEC_FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_every_fixture_is_valid(path):
    spec = Spec.model_validate_json(path.read_text(encoding="utf-8"))
    assert spec.sources


def test_multi_source_fixture_exposes_staging_and_gold_outputs():
    spec = Spec.model_validate(_load(MULTI))

    assert spec.sources[1].outputs["order_id"] == "bigint"
    assert spec.transform.outputs == {"order_date", "channel", "revenue", "order_count"}


def test_single_source_fixture_uses_real_columns_of_seeded_table():
    seed = (SPEC_FIXTURES_DIR.parents[1] / "infra/postgres/init/001_seed.sql").read_text(encoding="utf-8")
    body = re.search(r"CREATE TABLE IF NOT EXISTS sales_raw\.orders \((.*?)\);", seed, re.S).group(1)
    seeded = {line.split()[0] for line in body.strip().splitlines()}
    source = Spec.model_validate(_load(SINGLE)).sources[0]

    assert {c.from_ for c in source.staging.columns} <= seeded


def test_json_schema_file_is_up_to_date():
    from scripts.export_spec_schema import render_schema

    assert SCHEMA_PATH.read_text(encoding="utf-8") == render_schema(), "chạy scripts/export_spec_schema.py"


def test_merge_requires_primary_key():
    data = _load(MULTI)
    data["sources"][0]["primary_key"] = []
    _invalid(data, "primary_key")


def test_incremental_load_requires_cursor():
    data = _load(MULTI)
    data["sources"][1]["cursor_field"] = None
    _invalid(data, "cursor_field")


def test_filesystem_cursor_must_be_modification_date():
    data = _load(SINGLE)
    data["sources"][0] = {
        **data["sources"][0],
        "kind": "filesystem",
        "path_pattern": "refunds/*.csv",
        "format": "csv",
        "cursor_field": "updated_at",
        "write_disposition": "append",
    }
    data["sources"][0].pop("table")
    _invalid(data, "modification_date")


def test_kind_specific_fields_required():
    data = _load(MULTI)
    data["sources"][1].pop("pagination")
    _invalid(data, "pagination")


def test_rejects_duplicate_source_ids():
    data = _load(MULTI)
    data["sources"][1]["id"] = "orders"
    _invalid(data, "sources")


def test_rejects_duplicate_staging_output():
    data = _load(SINGLE)
    data["sources"][0]["staging"]["columns"][1]["as"] = "order_id"
    _invalid(data, "duy nhất")


def test_rejects_unknown_column_type():
    data = _load(SINGLE)
    data["sources"][0]["staging"]["columns"][0]["type"] = "int64"
    _invalid(data, "type")


def test_date_formats_only_for_date_types():
    data = _load(SINGLE)
    data["sources"][0]["staging"]["columns"][0]["date_formats"] = ["%Y"]
    _invalid(data, "date_formats")


def test_every_other_source_must_be_joined():
    data = _load(MULTI)
    data["transform"]["joins"] = []
    _invalid(data, "joins")


def test_join_on_must_not_be_empty():
    data = _load(MULTI)
    data["transform"]["joins"][0]["on"] = []
    _invalid(data, "on")


def test_join_must_use_staging_output_columns():
    data = _load(MULTI)
    data["transform"]["joins"][0]["on"] = [["orders.order_id", "payments.orderId"]]
    _invalid(data, "payments.orderId")


def test_multi_source_grain_must_be_qualified():
    data = _load(MULTI)
    data["transform"]["grain"] = ["order_date", "orders.channel"]
    _invalid(data, "grain")


def test_metric_must_reference_staging_output():
    data = _load(MULTI)
    data["transform"]["metrics"][0]["expr"] = "SUM(payments.total)"
    _invalid(data, "payments.total")


def test_string_literal_is_not_a_column_reference():
    data = _load(MULTI)
    data["transform"]["filters"] = ["orders.status <> 'x.y'"]

    assert Spec.model_validate(data).transform.filters == ["orders.status <> 'x.y'"]


def test_incremental_requires_unique_key():
    data = _load(MULTI)
    data["transform"]["unique_key"] = []
    _invalid(data, "unique_key")


def test_unique_key_must_be_gold_output():
    data = _load(MULTI)
    data["transform"]["unique_key"] = ["order_date", "status"]
    _invalid(data, "status")


def test_rejects_fields_outside_contract():
    data = _load(SINGLE)
    data["transform"]["sql"] = "SELECT 1"
    _invalid(data, "sql")
