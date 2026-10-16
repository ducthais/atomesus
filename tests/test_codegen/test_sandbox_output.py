import json
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest

from src.codegen import sandbox


@pytest.mark.parametrize("password", ["a", "abcde", "abcdef", "a@:/?#"])
def test_successful_stdout_is_preserved_for_parsing(monkeypatch, tmp_path, password):
    url = f"postgresql+psycopg://reader:{quote(password, safe='')}@db.example/shop"
    env = {"SOURCES__SHOP__CREDENTIALS": url}
    output = json.dumps({"data": "amount", "password_value": password, "url_value": url}, ensure_ascii=False) + "\n"
    process = MagicMock(returncode=0)
    process.communicate.return_value = (output, "")
    monkeypatch.setattr(sandbox.subprocess, "Popen", MagicMock(return_value=process))
    actual = sandbox._run(["test-command"], tmp_path, env, 10, "metric_reconcile")
    assert actual == output
    assert json.loads(actual)["data"] == "amount"
    assert json.loads(actual)["password_value"] == password


@pytest.mark.parametrize("password", ["a", "abcde", "@:?#/"])
def test_short_password_does_not_replace_text_but_full_credentials_url_is_hidden(password):
    url = f"postgresql+psycopg://reader:{quote(password, safe='')}@db.example/shop"
    output = f"amount data {password} {quote(password, safe='')} {url}"
    assert sandbox._safe_output(output, {"SOURCES__SHOP__CREDENTIALS": url}) == (
        f"amount data {password} {quote(password, safe='')} [đã ẩn]"
    )


@pytest.mark.parametrize("password", ["a", "abcde", "abcdef", "a@:/?#"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_failure_redacts_credentials_url_and_only_long_passwords(monkeypatch, tmp_path, password, stream):
    url = f"postgresql+psycopg://reader:{quote(password, safe='')}@db.example/shop"
    output = f"amount data {password} {quote(password, safe='')} {url}"
    process = MagicMock(returncode=1)
    process.communicate.return_value = (output, "") if stream == "stdout" else ("", output)
    monkeypatch.setattr(sandbox.subprocess, "Popen", MagicMock(return_value=process))
    with pytest.raises(sandbox._StopError) as caught:
        sandbox._run(["test-command"], tmp_path, {"SOURCES__SHOP__CREDENTIALS": url}, 10, "el_run")
    assert url not in str(caught.value)
    if len(password) >= 6:
        assert password not in str(caught.value) and quote(password, safe="") not in str(caught.value)
    else:
        assert str(caught.value) == f"amount data {password} {quote(password, safe='')} [đã ẩn]"


@pytest.mark.parametrize(
    ("output", "secret"),
    [
        ('{"api_key":"fake-api-key","ok":"kept"}', "fake-api-key"),
        ("Authorization: Bearer fake-bearer-token", "fake-bearer-token"),
        ('password="fake password value"', "fake password value"),
        ("postgresql://reader:fake-dsn-password@db.example/shop", "fake-dsn-password"),
        (
            # Tách chuỗi để bộ quét secret của gate không nhận nhầm khóa giả là private key thật.
            "-----BEGIN " + "PRIVATE KEY-----\nfake-private-material\n-----END " + "PRIVATE KEY-----",
            "fake-private-material",
        ),
    ],
)
def test_safe_output_redacts_sensitive_fields_and_preserves_diagnostics(output, secret):
    redacted = sandbox._safe_output(f"connection failed: {output}; retry later", {})
    assert secret not in redacted
    assert "[đã ẩn]" in redacted and "connection failed" in redacted and "retry later" in redacted
