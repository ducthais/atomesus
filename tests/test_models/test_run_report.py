import json

import pytest
from pydantic import ValidationError

from src.models.run_report import RunReport
from src.services.run_report_store import FIXTURES_DIR


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_every_fixture_matches_contract(path):
    report = RunReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert report.run_id


def test_deployed_fixture_has_signature_bound_to_files_hash_and_version():
    report = RunReport.model_validate(_load("deployed.json"))

    assert report.approval is not None
    assert report.approval.decision == "approved"
    assert report.approval.reviewer != report.created_by
    assert report.approval.files_hash == report.files_hash
    assert report.approval.version == report.version


def test_rejects_self_approval():
    data = _load("deployed.json")
    data["approval"]["reviewer"] = data["created_by"]

    with pytest.raises(ValidationError, match="tự duyệt"):
        RunReport.model_validate(data)


def test_rejects_approval_for_stale_files_hash():
    data = _load("deployed.json")
    data["files_hash"] = "sha256:changed"

    with pytest.raises(ValidationError, match="files_hash"):
        RunReport.model_validate(data)


def test_rejects_approval_for_old_version():
    data = _load("deployed.json")
    data["version"] = 2

    with pytest.raises(ValidationError, match="version"):
        RunReport.model_validate(data)


def test_deployed_status_requires_approval():
    data = _load("deployed.json")
    data["approval"] = None

    with pytest.raises(ValidationError, match="approval"):
        RunReport.model_validate(data)


def test_per_source_check_requires_source_id():
    data = _load("ready_for_review.json")
    data["checks"][0].pop("source_id")

    with pytest.raises(ValidationError, match="source_id"):
        RunReport.model_validate(data)


def test_model_check_must_not_have_source_id():
    data = _load("ready_for_review.json")
    dbt_test = next(c for c in data["checks"] if c["name"] == "dbt_test")
    dbt_test["source_id"] = "orders"

    with pytest.raises(ValidationError, match="source_id"):
        RunReport.model_validate(data)


def test_rejects_unknown_stage():
    data = _load("ready_for_review.json")
    data["stage_reached"] = "codegen"

    with pytest.raises(ValidationError):
        RunReport.model_validate(data)


def test_rejects_fields_outside_contract():
    data = _load("ready_for_review.json")
    data["reviewer"] = "an"

    with pytest.raises(ValidationError):
        RunReport.model_validate(data)
