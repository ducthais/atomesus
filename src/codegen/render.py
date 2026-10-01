"""P6 render: Spec → {đường dẫn: nội dung} bằng các template trong templates/ (SPEC §13).

Tất định, không gọi LLM. Mỗi Spec ra 4 + N file. Template dùng delimiter `[[ ]]`, `[% %]`, `[# #]`
để cú pháp Jinja của dbt (`{{ }}`, `{% %}`) đi thẳng ra file sinh mà không phải escape.

File sinh ra không parse được là bug template (`RenderError` → `INTERNAL_ERROR`), không phải lỗi agent.
"""

import ast
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from src.policy.models import Policy
from src.spec.models import Spec

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


class RenderError(Exception):
    """Template render lỗi hoặc ra file sai cú pháp."""


def staging_model(model_name: str, source_id: str) -> str:
    return f"stg_{model_name}__{source_id}"


def rejected_table(model_name: str, source_id: str) -> str:
    return f"{staging_model(model_name, source_id)}__rejected"


def output_paths(spec: Spec) -> dict[str, str]:
    """Vai trò → đường dẫn file sinh ra, theo bảng ở SPEC §13."""
    name = spec.transform.model_name
    paths = {
        "ingestion": f"ingestion/{name}_pipeline.py",
        "gold": f"dbt_project/models/gold/{name}.sql",
        "schema": f"dbt_project/models/gold/{name}.yml",
        "dagster": f"infra/dagster/generated/{name}_asset.py",
    }
    for source in spec.sources:
        paths[f"staging:{source.id}"] = f"dbt_project/models/staging/{staging_model(name, source.id)}.sql"
    return paths


def dlt_name(name: str) -> str:
    """Tên cột sau khi dlt chuẩn hóa sang snake_case (`orderId` → `order_id`), để staging đọc đúng cột bronze."""
    name = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        variable_start_string="[[",
        variable_end_string="]]",
        block_start_string="[%",
        block_end_string="%]",
        comment_start_string="[#",
        comment_end_string="#]",
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    env.filters.update(
        ident=_ident,
        sql_str=_sql_str,
        dlt_name=dlt_name,
        output_name=lambda ref: ref.rpartition(".")[2],
        pystr=json.dumps,  # chuỗi JSON cũng là literal Python hợp lệ
        pyindex=lambda key: f"[{json.dumps(key)}]",  # dlt.secrets[[ key | pyindex ]] → dlt.secrets["key"]
        pylist=lambda values: repr(list(values)),
        pydict=lambda value: repr(dict(value)),
    )
    env.tests["grain_named"] = lambda ref, name: ref.rpartition(".")[2] == name
    return env


def _namespace(value: Any) -> Any:
    """dict → SimpleNamespace để template viết `column.from`, `dedupe.keys` mà không đụng method của dict."""
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def render(spec: Spec, policy: Policy) -> dict[str, str]:
    """Render đủ 4 + N file của một Spec đã qua kiểm tra. Trả {đường dẫn: nội dung}."""
    domain = policy.domains.domains[spec.domain]
    name = spec.transform.model_name
    data = _namespace(spec.model_dump(by_alias=True, mode="json"))
    staging_keys = {}
    for source in spec.sources:
        dedupe = source.staging.dedupe
        keys = dedupe.keys if dedupe and dedupe.keys else source.primary_key
        outputs = {column.from_: column.as_ for column in source.staging.columns}
        staging_keys[source.id] = [outputs[key] for key in keys if key in outputs]
    context = {
        "spec": data,
        "model_name": name,
        "policy_sources": {ref: _namespace(decl.model_dump(by_alias=True)) for ref, decl in domain.sources.items()},
        "rates": domain.currency.rates,
        "staging_models": {source.id: staging_model(name, source.id) for source in spec.sources},
        "staging_keys": staging_keys,
        "gold_columns": [ref.rpartition(".")[2] for ref in spec.transform.grain]
        + [metric.name for metric in spec.transform.metrics],
    }
    paths = output_paths(spec)
    env = _environment()
    try:
        files = {
            paths["ingestion"]: env.get_template("dlt_pipeline.py.j2").render(context),
            paths["gold"]: env.get_template("dbt_model.sql.j2").render(context),
            paths["schema"]: env.get_template("dbt_schema.yml.j2").render(context),
            paths["dagster"]: env.get_template("dagster_asset.py.j2").render(context),
        }
        staging = env.get_template("dbt_staging.sql.j2")
        for source, raw in zip(spec.sources, data.sources, strict=True):
            files[paths[f"staging:{source.id}"]] = staging.render(
                context, source=raw, rejected_name=rejected_table(name, source.id)
            )
    except TemplateError as exc:
        raise RenderError(f"render template lỗi: {exc}") from exc
    check_syntax(files)
    return files


def check_syntax(files: dict[str, str]) -> None:
    """File Python phải compile được, YAML phải parse được. SQL được dbt compile ở bước chạy thử."""
    for path, content in files.items():
        try:
            if path.endswith(".py"):
                ast.parse(content, filename=path)
            elif path.endswith(".yml"):
                yaml.safe_load(content)
        except (SyntaxError, yaml.YAMLError) as exc:
            raise RenderError(f"{path}: file sinh ra sai cú pháp: {exc}") from exc


def file_hashes(files: dict[str, str]) -> tuple[dict[str, str], str]:
    """Hash từng file và `files_hash` của cả bộ (chữ ký duyệt gắn với giá trị này)."""
    hashes = {path: "sha256:" + hashlib.sha256(content.encode()).hexdigest() for path, content in sorted(files.items())}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return hashes, f"sha256:{digest}"
