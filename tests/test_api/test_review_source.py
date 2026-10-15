"""Nguồn `review_api` (domain rental): mock API `/reviews/feedback`, khóa chung với bảng pagila.* của pagila_pg.

Dữ liệu sinh từ seed Pagila (scripts/build_review_data.py), nên kiểm thẳng với file seed: không cần Postgres chạy.
"""

import gzip
import json

import pytest
from fastapi.testclient import TestClient

from infra.mock_api.app import FEEDBACK, FEEDBACK_FILE
from infra.mock_api.app import app as mock_api
from scripts import build_review_data
from src.pipeline import connectors, rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy


@pytest.fixture
def in_process(monkeypatch):
    monkeypatch.setattr(rest_api, "client", lambda url: TestClient(mock_api, base_url=url))
    return PipelineSettings(_env_file=None)


@pytest.fixture(scope="module")
def pagila():
    rentals, films = build_review_data.read_dump()
    return {r["rental_id"]: r for r in rentals}, films


def _decl():
    return load_policy().domains.domains["rental"].sources["review_api"]


def test_review_api_is_a_rest_source_of_the_rental_domain_with_its_own_openapi(in_process):
    found = connectors.discover(_decl(), in_process)
    (feedback,) = found["objects"]
    assert feedback["name"] == "feedback" and feedback["primary_key"] == ["feedback_id"]
    assert {c["name"]: c["type"] for c in feedback["columns"]} == {
        "feedback_id": "text",
        "rental_id": "integer",
        "customer_id": "integer",
        "film_id": "integer",
        "rating": "text",
        "would_recommend": "boolean",
        "comment": "text",
        "submitted_at": "timestamp with time zone",
    }


def test_reads_every_page_and_counts_duplicates(in_process):
    endpoint = rest_api.endpoints(_decl(), in_process)["feedback"]
    assert (endpoint["records_path"], endpoint["pagination"]) == ("data", "page")
    rows = list(rest_api.records(_decl(), in_process, endpoint))
    assert len(rows) == len(FEEDBACK) == 4764
    assert rest_api.count(rows, ["feedback_id"]) == 4717, "47 bản ghi gửi lại trùng feedback_id"
    last = TestClient(mock_api).get("/reviews/feedback", params={"page": 48, "page_size": 100}).json()
    assert len(last["data"]) == 64 and last["total"] == 4764
    assert TestClient(mock_api).get("/reviews/feedback", params={"page_size": 101}).status_code == 422


def test_shared_keys_match_pagila_rentals_customers_and_films(pagila):
    rentals, films = pagila
    unique = list({row["feedback_id"]: row for row in FEEDBACK}.values())
    orphans = [row for row in unique if row["rental_id"] not in rentals]
    assert len(orphans) == 14 and all(row["rental_id"] > max(rentals) for row in orphans)
    for row in unique:
        rental = rentals.get(row["rental_id"])
        if rental is None:
            continue
        assert rental["returned"] is not None and row["customer_id"] == rental["customer_id"]
        assert row["film_id"] == films[rental["inventory_id"]]
        assert row["submitted_at"] > rental["returned"].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert sum(row["rating"] == "N/A" for row in unique) == 24
    assert {row["rating"] for row in unique} == {"1", "2", "3", "4", "5", "N/A"}
    assert len({row["customer_id"] for row in unique}) == 599, "mọi khách Pagila đều có phản hồi"


def test_committed_feedback_file_matches_a_fresh_build():
    fresh = json.dumps(build_review_data.build(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert gzip.decompress(FEEDBACK_FILE.read_bytes()) == fresh, "chạy lại scripts/build_review_data.py"
