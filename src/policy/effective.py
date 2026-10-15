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


def read_upload_selections(store_path: str, owner_id: str | None = None) -> list[dict]:
    """Store cũ chưa có uploads vẫn dùng policy hiện tại; không tạo DB khi đọc."""
    path = Path(store_path).resolve()
    if not path.exists():
        return []
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'uploads'").fetchone():
                return []
            query = "SELECT id, owner_id, metadata, selected_json FROM uploads"
            params = ()
            if owner_id is not None:
                query += " WHERE owner_id = ?"
                params = (owner_id,)
            rows = conn.execute(query + " ORDER BY id", params).fetchall()
        selections = []
        for upload_id, owner, metadata_json, selected_json in rows:
            try:
                metadata, selected = json.loads(metadata_json), json.loads(selected_json)
                if not selected:
                    continue
                selections.append(
                    {
                        "id": upload_id,
                        "owner_id": owner,
                        "label": metadata["name"],
                        "schema": "files",
                        "tables": selected,
                        "root": metadata["root"],
                        "email": None,
                    }
                )
            except (ValueError, TypeError, KeyError):
                logger.warning("Bỏ qua lựa chọn file đã lưu: metadata không hợp lệ.")
        return selections
    except sqlite3.Error as exc:
        raise PolicyError("Không đọc được lựa chọn file đã lưu trong store.") from exc


def without_hidden(policy: Policy) -> Policy:
    """Chính sách cho các luồng thao tác mới: bỏ domain `hidden: true` cùng mục tiêu và luật trỏ tới nguồn của chúng.

    Run cũ, pipeline đã deploy, Day 2 và các thao tác trên run có sẵn vẫn dùng chính sách đầy đủ.
    """
    hidden = {name for name, domain in policy.domains.domains.items() if domain.hidden}
    if not hidden:
        return policy
    refs = {ref for name in hidden for ref in policy.domains.domains[name].sources}
    raw = policy.model_dump()
    for name in hidden:
        raw["domains"]["domains"].pop(name)
        raw["objectives"].pop(name, None)
    rules = raw["budgets"]["rules"]
    rules["require_partition_filter_on"] = [
        ref for ref in rules["require_partition_filter_on"] if ref.partition(".")[0] not in refs
    ]
    return Policy.model_validate(raw)


def effective_policy(config: "PipelineSettings", owner_id: str | None = None) -> Policy:
    """None dành cho hệ thống/Reviewer; Engineer chỉ nhận domain YAML và domain của mình."""
    policy = load_policy()
    selections = read_user_selections(config.store_path, owner_id) + read_upload_selections(config.store_path, owner_id)
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
            name, source_ref = f"u_{prefix}", f"{'f' if 'root' in selection else 'c'}_{prefix}"
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
        source = (
            {"kind": "filesystem", "connection_ref": f"upload:{connection_id}", "root": selection["root"]}
            if "root" in selection
            else {"kind": "postgres", "connection_ref": f"conn:{connection_id}", "schema": selection["schema"]}
        )
        domain = {
            "label": selection["label"],
            "owner_id": selection["owner_id"],
            "datasets": [selection["schema"]],
            "steward": selection["email"] or f"{selection['owner_id']}@users.local",
            "currency": policy.domains.user_defaults.currency.model_dump(),
            "sources": {source_ref: {**source, "tables": selection["tables"]}},
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
