"""N1: mỗi pipeline đã deploy chỉ nhận credential của đúng các nguồn nó đọc (gồm review_api ⋈ pagila_pg)."""

import json
import os

import pytest

from src.dagster_runtime import secrets
from src.dagster_runtime.secrets import CredentialsUnavailableError, read_model_env, source_refs, sync_secrets
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from tests.test_dagster_runtime.specs import RENTAL_MODEL, REVIEW_MODEL, install, rental_spec, review_spec

PAGILA = "SOURCES__PAGILA_PG__CREDENTIALS"
REVIEW = "SOURCES__REVIEW_API__BASE_URL"


@pytest.fixture
def settings():
    return PipelineSettings(
        _env_file=None,
        database_url="postgresql://pipeline_reader:reader-secret@postgres:5432/pipeline_demo",
        mock_api_url="http://mock-api:8100",
    )


def test_source_refs_come_from_the_signed_ingestion_file(tmp_path):
    assert source_refs(install(tmp_path, review_spec())) == {"pagila_pg", "review_api"}
    assert source_refs(install(tmp_path, rental_spec())) == {"pagila_pg"}


def test_each_pipeline_file_has_only_its_own_sources(tmp_path, settings):
    deploy, out = tmp_path / "deploy", tmp_path / "secrets"
    install(deploy, review_spec())
    install(deploy, rental_spec())

    result = sync_secrets(deploy, out, load_policy(), settings)

    assert result == {REVIEW_MODEL: None, RENTAL_MODEL: None}
    review = read_model_env(REVIEW_MODEL, out)
    rental = read_model_env(RENTAL_MODEL, out)
    assert set(review) == {PAGILA, REVIEW} and set(rental) == {PAGILA}
    assert review[REVIEW] == "http://mock-api:8100/reviews", "review_api không có secret, chỉ có địa chỉ gốc"
    assert review[PAGILA].startswith("postgresql+psycopg://pipeline_reader:")
    assert sorted(p.name for p in out.iterdir()) == [f"{RENTAL_MODEL}.json", f"{REVIEW_MODEL}.json"]


@pytest.mark.skipif(os.name == "nt", reason="Windows không có quyền POSIX")
def test_credential_files_are_private(tmp_path, settings):
    deploy, out = tmp_path / "deploy", tmp_path / "secrets"
    install(deploy, rental_spec())

    sync_secrets(deploy, out, load_policy(), settings)

    assert (out / f"{RENTAL_MODEL}.json").stat().st_mode & 0o777 == 0o600
    assert out.stat().st_mode & 0o777 == 0o700


def test_removed_legacy_and_broken_pipelines_have_no_file(tmp_path, settings, monkeypatch):
    deploy, out = tmp_path / "deploy", tmp_path / "secrets"
    out.mkdir()
    (out / "fct_gone.json").write_text("{}", encoding="utf-8")
    install(deploy, review_spec())
    install(deploy, rental_spec())
    legacy = deploy / "fct_old" / "infra" / "dagster" / "generated"
    legacy.mkdir(parents=True)
    (legacy / "fct_old_asset.py").write_text("x = 1\n", encoding="utf-8")
    original = secrets.model_env

    def broken(refs, policy, config):
        if "review_api" in refs:
            raise CredentialsUnavailableError("Nguồn 'review_api' không có trong domains.yml.")
        return original(refs, policy, config)

    monkeypatch.setattr(secrets, "model_env", broken)
    result = sync_secrets(deploy, out, load_policy(), settings)

    assert result == {REVIEW_MODEL: "Nguồn 'review_api' không có trong domains.yml.", RENTAL_MODEL: None}
    assert sorted(p.name for p in out.iterdir()) == [f"{RENTAL_MODEL}.json"], (
        "lỗi một pipeline không chặn pipeline khác"
    )


def test_unconfigured_connection_reports_only_the_error_type(tmp_path):
    deploy, out = tmp_path / "deploy", tmp_path / "secrets"
    install(deploy, rental_spec())

    result = sync_secrets(deploy, out, load_policy(), PipelineSettings(_env_file=None, database_url=""))

    assert result[RENTAL_MODEL] == "_StopError" and not (out / f"{RENTAL_MODEL}.json").exists()


def test_rewrites_only_when_content_changes(tmp_path, settings):
    deploy, out = tmp_path / "deploy", tmp_path / "secrets"
    install(deploy, rental_spec())
    sync_secrets(deploy, out, load_policy(), settings)
    path = out / f"{RENTAL_MODEL}.json"
    os.utime(path, (1, 1))

    sync_secrets(deploy, out, load_policy(), settings)

    assert path.stat().st_mtime == 1
    changed = settings.model_copy(update={"database_url": settings.database_url.__class__("postgresql://u:v@h/db")})
    sync_secrets(deploy, out, load_policy(), changed)
    assert path.stat().st_mtime != 1 and "u:v@h" in json.loads(path.read_text(encoding="utf-8"))[PAGILA]


@pytest.mark.parametrize("model", ["../x", "X", "", "a/b", None])
def test_read_model_env_rejects_unsafe_names(tmp_path, model):
    with pytest.raises(CredentialsUnavailableError, match="không hợp lệ"):
        read_model_env(model, tmp_path)


def test_read_model_env_reports_missing_and_malformed_files(tmp_path):
    with pytest.raises(CredentialsUnavailableError, match="Chưa có credential"):
        read_model_env("fct_x", tmp_path)
    (tmp_path / "fct_x.json").write_text('{"A": 1}', encoding="utf-8")
    with pytest.raises(CredentialsUnavailableError, match="sai định dạng"):
        read_model_env("fct_x", tmp_path)


def test_secrets_dir_defaults_to_a_temp_folder(monkeypatch, tmp_path):
    monkeypatch.delenv(secrets.SECRETS_DIR_ENV, raising=False)
    assert secrets.secrets_dir().name == "atomesus-secrets"
    monkeypatch.setenv(secrets.SECRETS_DIR_ENV, str(tmp_path))
    assert secrets.secrets_dir() == tmp_path
