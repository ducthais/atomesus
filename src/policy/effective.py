"""Ghép lựa chọn nguồn của người dùng vào chính sách YAML, chỉ đọc metadata trong store."""

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from src.policy.loader import PolicyError, _format, load_policy
from src.policy.models import Domain, Policy

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.pipeline.settings import PipelineSettings


def read_user_selections(store_path: str, owner_id: str | None = None) -> list[dict]:
    """Không tạo store hay chạy migration; store cũ chưa có lựa chọn thì chỉ dùng YAML."""
    path = Path(store_path).resolve()
    if not path.exists():
        return []
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"connections", "connection_selections"} <= tables:
                return []
            has_email = "users" in tables and any(row[1] == "email" for row in conn.execute("PRAGMA table_info(users)"))
            email = "u.email" if has_email else "NULL"
            query = (
                f"SELECT c.id, c.owner_id, c.name, s.schema_name, s.tables_json, {email}"
                " FROM connections c JOIN connection_selections s ON s.connection_id = c.id"
            )
            if has_email:
                query += " LEFT JOIN users u ON u.id = c.owner_id"
            params = ()
            if owner_id is not None:
                query += " WHERE c.owner_id = ?"
                params = (owner_id,)
            rows = conn.execute(query + " ORDER BY c.id", params).fetchall()
        selections = []
        for row in rows:
            try:
                selected_tables = json.loads(row[4])
            except (ValueError, TypeError):
                logger.warning("Bỏ qua lựa chọn nguồn đã lưu: danh sách bảng không phải JSON hợp lệ.")
                continue
            selections.append(
                {
                    "id": row[0],
                    "owner_id": row[1],
                    "label": row[2],
                    "schema": row[3],
                    "tables": selected_tables,
                    "email": row[5],
                }
            )
        return selections
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise PolicyError("Không đọc được lựa chọn nguồn đã lưu trong store.") from exc


def effective_policy(config: "PipelineSettings", owner_id: str | None = None) -> Policy:
    """None dành cho hệ thống/Reviewer; Engineer chỉ nhận domain YAML và domain của mình."""
    policy = load_policy()
    selections = read_user_selections(config.store_path, owner_id)
    if not selections:
        return policy
    template = policy.objectives.get("_user_default")
    if template is None:
        raise PolicyError("objectives.yml thiếu mẫu _user_default cho domain người dùng.")
    raw = policy.model_dump()
    domains = raw["domains"]["domains"]
    source_refs = {ref for domain in domains.values() for ref in domain["sources"]}
    for selection in selections:
        connection_id = selection["id"]
        for length in (8, 12, len(connection_id)):
            prefix = connection_id[:length]
            name, source_ref = f"u_{prefix}", f"c_{prefix}"
            if (
                name not in domains
                and source_ref not in source_refs
                and sum(item["id"][:length] == prefix for item in selections) == 1
            ):
                break
        else:
            logger.warning("Bỏ qua lựa chọn nguồn đã lưu: không tạo được tên domain và nguồn riêng biệt.")
            continue
        if length != 8:
            logger.warning("Dùng tiền tố id dài hơn cho lựa chọn nguồn đã lưu bị trùng tên.")
        if not isinstance(selection["schema"], str) or not selection["schema"].strip():
            logger.warning("Bỏ qua lựa chọn nguồn đã lưu: schema rỗng hoặc không hợp lệ.")
            continue
        if not isinstance(selection["tables"], list):
            logger.warning("Bỏ qua lựa chọn nguồn đã lưu: phải có danh sách bảng.")
            continue
        domain = {
            "label": selection["label"],
            "owner_id": selection["owner_id"],
            "datasets": [selection["schema"]],
            "steward": selection["email"] or f"{selection['owner_id']}@users.local",
            "currency": policy.domains.user_defaults.currency.model_dump(),
            "sources": {
                source_ref: {
                    "kind": "postgres",
                    "connection_ref": f"conn:{connection_id}",
                    "schema": selection["schema"],
                    "tables": selection["tables"],
                }
            },
        }
        try:
            domain = Domain.model_validate(domain).model_dump()
        except ValidationError:
            logger.warning("Bỏ qua lựa chọn nguồn đã lưu: domain không hợp lệ.")
            continue
        domains[name] = domain
        source_refs.add(source_ref)
        raw["objectives"][name] = template.model_dump()
    try:
        return Policy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyError(f"Chính sách hiệu lực không hợp lệ: {_format(exc)}") from exc
