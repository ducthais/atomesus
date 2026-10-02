from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from src.pipeline import connectors, postgres
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy.loader import PolicyError, load_policy
from src.policy.models import Policy

router = APIRouter(prefix="/catalog", tags=["catalog"], dependencies=[Depends(current_user)])


def _policy() -> Policy:
    try:
        return load_policy()
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc


def _location(decl) -> str | None:
    return decl.schema_ or decl.openapi or decl.root


@router.get("")
def read_catalog(config: PipelineSettings = Depends(settings)) -> dict:
    """Domain → nguồn → bảng/cột thật, kèm steward và thực thể từ domains.yml. Chỉ đọc, không gọi LLM.

    Nguồn có connector được kết nối để đọc schema; nguồn chưa hỗ trợ hoặc không kết nối được
    vẫn có mặt với `available: false` và lý do, để giao diện catalog hiện đủ những gì đã khai báo.
    """
    policy = _policy()
    domains = {}
    for name, domain in policy.domains.domains.items():
        sources = {}
        for ref, decl in domain.sources.items():
            entry: dict = {
                "kind": decl.kind,
                "connection_ref": decl.connection_ref,
                "location": _location(decl),
                "available": False,
                "tables": {},
                "primary_keys": {},
                "foreign_keys": [],
            }
            try:
                found = connectors.discover(decl, config)
                entry["tables"] = {obj["name"]: obj["columns"] for obj in found["objects"]}
                entry["primary_keys"] = {
                    obj["name"]: obj["primary_key"] for obj in found["objects"] if obj["primary_key"]
                }
                entry["foreign_keys"] = found["foreign_keys"]
                entry["available"] = True
            except connectors.UnsupportedSourceError:
                entry["reason"] = f"Nguồn {decl.kind} chưa hỗ trợ ở Sprint 1"
            except postgres.SourceUnavailableError as exc:
                entry["reason"] = str(exc)
            sources[ref] = entry
        domains[name] = {
            "steward": domain.steward,
            "currency": domain.currency.base,
            "sources": sources,
            "entities": {key: entity.model_dump() for key, entity in domain.entities.items()},
        }
    return {"domains": domains, "conformed_dimensions": policy.domains.conformed_dimensions}


@router.get("/sources")
def list_sources() -> list[dict]:
    """Các kết nối khai báo trong domains.yml. Không kết nối tới nguồn; dùng để chọn nguồn trước khi khám phá."""
    return [
        {
            "ref": ref,
            "domain": name,
            "kind": decl.kind,
            "connection_ref": decl.connection_ref,
            "location": _location(decl),
            "supported": connectors.supported(decl.kind),
        }
        for name, domain in _policy().domains.domains.items()
        for ref, decl in domain.sources.items()
    ]


@router.get("/sources/{ref}/objects")
def source_objects(ref: str, config: PipelineSettings = Depends(settings)) -> dict:
    """Kết nối trực tiếp tới nguồn lúc được gọi và trả các bảng (hoặc collection) đang có, kèm cột và khóa."""
    declared = {
        r: (name, decl) for name, domain in _policy().domains.domains.items() for r, decl in domain.sources.items()
    }
    if ref not in declared:
        raise HTTPException(404, f"Nguồn '{ref}' không có trong domains.yml.")
    domain, decl = declared[ref]
    try:
        found = connectors.discover(decl, config)
    except connectors.UnsupportedSourceError as exc:
        raise HTTPException(422, str(exc)) from exc
    except postgres.SourceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "ref": ref,
        "domain": domain,
        "kind": decl.kind,
        "location": _location(decl),
        "discovered_at": datetime.now(UTC).isoformat(),
        **found,
    }
