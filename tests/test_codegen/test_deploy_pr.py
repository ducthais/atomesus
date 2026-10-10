"""P16: mở pull request vào repo pipeline qua GitHub giả trong bộ nhớ (transport httpx, không ra mạng)."""

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from src.codegen.deploy import branch_name, open_pull_request
from src.codegen.github import GithubClient, GithubError, blob_sha
from src.codegen.render import file_hashes, render
from src.deployer.manifest import ManifestError, verify_manifest
from src.policy.loader import load_policy
from src.run_report import FIXTURES_DIR, Approval, RunReport
from src.spec import SPEC_FIXTURES_DIR, Spec

TOKEN = "github_pat_token_bi_mat_khong_duoc_lo"
REPO = "acme/atomesus-pipelines"
FOLDER = "pipelines/fct_daily_orders"


class FakeGithub:
    """GitHub giả: đủ các API mà GithubClient dùng, lưu repo trong bộ nhớ và ghi lại mọi request.

    `fail`: {"METHOD đường-dẫn-đầu": mã HTTP hoặc "timeout"} để giả lập lỗi. `race_pull`: POST /pulls tạo pull request
    rồi trả 422, như khi một request Deploy khác vừa mở trước. `race_ref(sha commit vừa tạo) -> sha`: POST /git/refs
    thấy request khác vừa tạo nhánh trỏ vào commit trả về rồi trả 422.
    """

    def __init__(self, fail: dict | None = None, race_pull: bool = False):
        self.refs = {"main": "c0"}
        self.commits = {"c0": {"tree": "t0", "parents": [], "message": "init"}}
        self.trees = {"t0": {"README.md": "# pipelines\n"}}
        self.pulls: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.fail = fail or {}
        self.race_pull = race_pull
        self.race_ref = None

    def client(self) -> GithubClient:
        return GithubClient(REPO, SecretStr(TOKEN), transport=httpx.MockTransport(self.handle))

    def files_at(self, branch: str) -> dict[str, str]:
        return self.trees[self.commits[self.refs[branch]]["tree"]]

    def writes(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if r[0] != "GET"]

    def _pull(self, head: str, base: str, title: str, body: str) -> dict:
        number = len(self.pulls) + 1
        pull = {
            "number": number,
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "head": {"ref": head, "sha": self.refs[head]},
            "base": {"ref": base},
            "state": "open",
            "title": title,
            "body": body,
            "created_at": "2026-10-10T08:00:00Z",
        }
        self.pulls.append(pull)
        return pull

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.url.path.startswith(f"/repos/{REPO}")
        method, path = request.method, request.url.path.removeprefix(f"/repos/{REPO}")
        self.requests.append((method, path))
        for key, status in self.fail.items():
            if f"{method} {path}".startswith(key):
                if status == "timeout":
                    raise httpx.ReadTimeout("hết giờ", request=request)
                return httpx.Response(status, json={"message": f"lỗi giả {status} token={TOKEN}"})
        body = json.loads(request.content) if request.content else {}
        if method == "GET" and path == "":
            return httpx.Response(200, json={"default_branch": "main", "html_url": f"https://github.com/{REPO}"})
        if method == "GET" and path.startswith("/git/ref/heads/"):
            branch = path.removeprefix("/git/ref/heads/")
            if branch not in self.refs:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": self.refs[branch]}})
        if method == "GET" and path.startswith("/git/commits/"):
            sha = path.removeprefix("/git/commits/")
            return httpx.Response(200, json={"sha": sha, "tree": {"sha": self.commits[sha]["tree"]}})
        if method == "GET" and path.startswith("/git/trees/"):
            tree = self.trees[path.removeprefix("/git/trees/")]
            entries = [{"path": p, "type": "blob", "sha": blob_sha(c)} for p, c in tree.items()]
            return httpx.Response(200, json={"tree": entries, "truncated": False})
        if method == "GET" and path.startswith("/git/blobs/"):
            sha = path.removeprefix("/git/blobs/")
            content = next(c for tree in self.trees.values() for c in tree.values() if blob_sha(c) == sha)
            encoded = base64.b64encode(content.encode()).decode()
            return httpx.Response(200, json={"sha": sha, "content": encoded, "encoding": "base64"})
        if method == "POST" and path == "/git/trees":
            tree = dict(self.trees[body["base_tree"]])
            for entry in body["tree"]:
                if "content" in entry:
                    tree[entry["path"]] = entry["content"]
                elif entry.get("sha") is None:
                    tree.pop(entry["path"], None)
            sha = f"t{len(self.trees)}"
            self.trees[sha] = tree
            return httpx.Response(201, json={"sha": sha})
        if method == "POST" and path == "/git/commits":
            sha = f"c{len(self.commits)}"
            self.commits[sha] = {"tree": body["tree"], "parents": body["parents"], "message": body["message"]}
            return httpx.Response(201, json={"sha": sha})
        if method == "POST" and path == "/git/refs":
            branch = body["ref"].removeprefix("refs/heads/")
            if self.race_ref is not None and branch not in self.refs:
                self.refs[branch] = self.race_ref(body["sha"])
            if branch in self.refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.refs[branch] = body["sha"]
            return httpx.Response(201, json={"ref": body["ref"], "object": {"sha": body["sha"]}})
        if method == "GET" and path == "/pulls":
            owner, branch = request.url.params["head"].split(":", 1)
            assert owner == "acme" and request.url.params["state"] == "open"
            return httpx.Response(
                200, json=[p for p in self.pulls if p["head"]["ref"] == branch and p["state"] == "open"]
            )
        if method == "POST" and path == "/pulls":
            exists = any(p["head"]["ref"] == body["head"] and p["state"] == "open" for p in self.pulls)
            if self.race_pull and not exists:
                self._pull(body["head"], body["base"], "từ request khác", "")
                exists = True
            if exists:
                return httpx.Response(
                    422, json={"message": "Validation Failed", "errors": [{"message": "A pull request already exists"}]}
                )
            return httpx.Response(201, json=self._pull(body["head"], body["base"], body["title"], body["body"]))
        raise AssertionError(f"GithubClient gọi API ngoài danh sách cho phép: {method} {path}")


def approved_report() -> tuple[RunReport, dict[str, str]]:
    """Run đã duyệt với bộ file sinh thật từ spec mẫu."""
    spec = Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
    files = render(spec, load_policy())
    hashes, files_hash = file_hashes(files)
    ready = RunReport.model_validate_json((FIXTURES_DIR / "ready_for_review.json").read_text(encoding="utf-8"))
    approval = Approval(
        decision="approved",
        reviewer="an",
        files_hash=files_hash,
        version=ready.version,
        at=datetime(2026, 10, 10, 7, 30, tzinfo=UTC),
        comment="Bằng chứng đủ",
    )
    report = ready.model_copy(
        update={
            "status": "APPROVED",
            "stage_reached": "review",
            "spec": spec.model_dump(by_alias=True, mode="json"),
            "files": hashes,
            "files_hash": files_hash,
            "approval": approval,
        }
    )
    return report, files


def test_pull_request_contains_signed_files_report_and_manifest():
    report, files = approved_report()
    github = FakeGithub()

    deployment = open_pull_request(report, files, github.client())

    branch = branch_name("fct_daily_orders", report.version)
    assert branch == "pipeline/fct_daily_orders/v3"
    assert deployment.model_dump(exclude={"opened_at"}) == {
        "repo": REPO,
        "branch": branch,
        "pr_number": 1,
        "pr_url": f"https://github.com/{REPO}/pull/1",
        "head_sha": github.refs[branch],
        "state": "open",
    }
    commit = github.commits[github.refs[branch]]
    assert commit["parents"] == ["c0"], "nhánh tạo từ đầu nhánh mặc định"
    assert sum(path == "/git/commits" for method, path in github.writes()) == 1, "đúng một commit"
    tree = github.files_at(branch)
    assert tree["README.md"] == "# pipelines\n", "giữ nguyên phần còn lại của repo"
    in_folder = {p.removeprefix(f"{FOLDER}/"): c for p, c in tree.items() if p.startswith(f"{FOLDER}/")}
    assert set(in_folder) == {*files, "run_report.json", "manifest.json"}
    assert {p: in_folder[p] for p in files} == files
    saved = RunReport.model_validate_json(in_folder["run_report.json"])
    assert saved == report
    manifest = json.loads(in_folder["manifest.json"])
    verify_manifest(manifest, {p: in_folder[p] for p in files})
    assert manifest["run_id"] == report.run_id and manifest["version"] == report.version
    assert manifest["model_name"] == "fct_daily_orders" and manifest["files_hash"] == report.files_hash
    assert manifest["files"] == report.files and manifest["reviewer"] == "an"
    assert manifest["approved_at"] == report.approval.at.isoformat()
    pull = github.pulls[0]
    assert pull["base"]["ref"] == "main" and pull["head"]["ref"] == branch
    for expected in (report.run_id, report.files_hash, "`an`", report.approval.at.isoformat(), "Bằng chứng đủ"):
        assert expected in pull["body"]
    assert f"https://github.com/{REPO}/blob/{branch}/{FOLDER}/run_report.json" in pull["body"]


def test_deploy_again_returns_open_pull_request_without_new_branch_commit_or_pr():
    report, files = approved_report()
    github = FakeGithub()
    first = open_pull_request(report, files, github.client())
    writes = len(github.writes())

    again = open_pull_request(report, files, github.client())

    assert again.pr_number == first.pr_number and again.pr_url == first.pr_url
    assert len(github.writes()) == writes, "không tạo nhánh, commit hay pull request mới"
    assert len(github.pulls) == 1


def test_existing_branch_without_pull_request_gets_one_on_that_branch():
    report, files = approved_report()
    github = FakeGithub(fail={"POST /pulls": 500})
    with pytest.raises(GithubError):
        open_pull_request(report, files, github.client())
    branch = branch_name("fct_daily_orders", report.version)
    head, writes = github.refs[branch], len(github.writes())
    github.fail = {}

    deployment = open_pull_request(report, files, github.client())

    assert github.writes()[writes:] == [("POST", "/pulls")], "không tạo nhánh hay commit mới"
    assert deployment.head_sha == head and github.pulls[0]["head"]["ref"] == branch


def test_concurrent_deploy_reuses_pull_request_opened_by_other_request():
    report, files = approved_report()
    github = FakeGithub(race_pull=True)

    deployment = open_pull_request(report, files, github.client())

    assert len(github.pulls) == 1 and deployment.pr_number == 1


def signed_copy(report: RunReport, files: dict[str, str], **update) -> RunReport:
    """Bản duyệt khác của cùng model và version: chữ ký gắn với đúng `files`."""
    hashes, files_hash = file_hashes(files)
    approval = report.approval.model_copy(update={"files_hash": files_hash})
    return report.model_copy(update={"files": hashes, "files_hash": files_hash, "approval": approval, **update})


def other_runs():
    """Run khác cùng model và version: cùng bộ file nhưng khác run_id, hoặc cùng run_id nhưng bộ file khác."""
    report, files = approved_report()
    changed = {**files, next(iter(files)): files[next(iter(files))] + "\n-- bản khác\n"}
    return [
        (report, files, signed_copy(report, files, run_id="9d1e2f3a-0000-4000-8000-000000000002"), files),
        (report, files, signed_copy(report, changed), changed),
    ]


@pytest.mark.parametrize(("first", "first_files", "second", "second_files"), other_runs())
def test_open_pull_request_of_other_signed_run_is_never_reused(first, first_files, second, second_files):
    github = FakeGithub()
    open_pull_request(first, first_files, github.client())
    writes = len(github.writes())

    with pytest.raises(ManifestError) as caught:
        open_pull_request(second, second_files, github.client())

    assert "khác bộ file đã duyệt" in str(caught.value)
    assert len(github.writes()) == writes and len(github.pulls) == 1


@pytest.mark.parametrize(("first", "first_files", "second", "second_files"), other_runs())
def test_branch_of_other_signed_run_without_pull_request_is_not_reused(first, first_files, second, second_files):
    github = FakeGithub(fail={"POST /pulls": 500})
    with pytest.raises(GithubError):
        open_pull_request(first, first_files, github.client())
    github.fail = {}
    writes = len(github.writes())

    with pytest.raises(ManifestError):
        open_pull_request(second, second_files, github.client())

    assert len(github.writes()) == writes and github.pulls == []


def test_branch_with_unrelated_content_is_not_reused():
    report, files = approved_report()
    github = FakeGithub()
    github.refs[branch_name("fct_daily_orders", report.version)] = "c0"

    with pytest.raises(ManifestError):
        open_pull_request(report, files, github.client())

    assert github.writes() == [] and github.pulls == []


def test_pull_request_changed_after_opening_is_not_returned():
    report, files = approved_report()
    github = FakeGithub()
    open_pull_request(report, files, github.client())
    branch = branch_name("fct_daily_orders", report.version)
    tree = dict(github.files_at(branch))
    tree[f"{FOLDER}/{next(iter(files))}"] = "-- sửa thẳng trên nhánh\n"
    github.trees["t_sua"] = tree
    github.commits["c_sua"] = {"tree": "t_sua", "parents": [github.refs[branch]], "message": "sửa"}
    github.refs[branch] = "c_sua"

    with pytest.raises(ManifestError):
        open_pull_request(report, files, github.client())


def test_branch_created_concurrently_with_same_signed_files_is_used():
    report, files = approved_report()
    github = FakeGithub()
    github.race_ref = lambda sha: sha  # request khác cùng run tạo nhánh trỏ vào commit cùng nội dung

    deployment = open_pull_request(report, files, github.client())

    assert deployment.pr_number == 1 and len(github.pulls) == 1


@pytest.mark.parametrize(("first", "first_files", "second", "second_files"), other_runs())
def test_branch_created_concurrently_by_other_run_is_not_used(first, first_files, second, second_files):
    github = FakeGithub(fail={"POST /pulls": 500})
    with pytest.raises(GithubError):
        open_pull_request(first, first_files, github.client())
    branch = branch_name("fct_daily_orders", first.version)
    winner = github.refs.pop(branch)
    github.fail = {}
    github.race_ref = lambda sha: winner

    with pytest.raises(ManifestError):
        open_pull_request(second, second_files, github.client())

    assert github.refs[branch] == winner and github.pulls == []


def test_files_of_previous_version_no_longer_signed_are_removed():
    report, files = approved_report()
    github = FakeGithub()
    github.trees["t0"] = {"README.md": "x\n", f"{FOLDER}/old_model.sql": "select 1\n", "pipelines/other/a.sql": "a\n"}

    open_pull_request(report, files, github.client())

    tree = github.files_at(branch_name("fct_daily_orders", report.version))
    assert f"{FOLDER}/old_model.sql" not in tree
    assert tree["pipelines/other/a.sql"] == "a\n", "không đụng pipeline khác"


def test_files_not_matching_signature_never_reach_github():
    report, files = approved_report()
    github = FakeGithub()
    tampered = {**files, next(iter(files)): "-- sửa sau khi duyệt\n"}

    with pytest.raises(ManifestError):
        open_pull_request(report, tampered, github.client())

    assert github.requests == []


@pytest.mark.parametrize(
    ("fail", "status_code", "keyword"),
    [
        (401, 502, "401"),
        (403, 502, "quyền"),
        (404, 502, "PIPELINE_GITHUB_REPO"),
        (422, 502, "422"),
        (500, 503, "GitHub đang lỗi"),
        (502, 503, "GitHub đang lỗi"),
        ("timeout", 503, "không trả lời"),
    ],
)
def test_github_errors_become_clear_messages_without_token(fail, status_code, keyword):
    report, files = approved_report()
    github = FakeGithub(fail={"POST /git/trees" if fail == 422 else "GET": fail})

    with pytest.raises(GithubError) as caught:
        open_pull_request(report, files, github.client())

    assert caught.value.status_code == status_code
    assert keyword in caught.value.message
    assert TOKEN not in caught.value.message and TOKEN not in str(caught.value)
    # Lỗi httpx gốc giữ request kèm header Authorization: không được nối vào traceback.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__


@pytest.mark.parametrize(
    ("repo", "token", "missing"),
    [
        ("", "", "PIPELINE_GITHUB_TOKEN và PIPELINE_GITHUB_REPO"),
        (REPO, "", "PIPELINE_GITHUB_TOKEN"),
        ("", TOKEN, "PIPELINE_GITHUB_REPO"),
        ("khong-co-dau-gach", TOKEN, "owner/tên-repo"),
    ],
)
def test_missing_configuration_is_503_before_any_request(repo, token, missing):
    calls = []
    client = GithubClient(repo, SecretStr(token), transport=httpx.MockTransport(lambda r: calls.append(r)))
    report, files = approved_report()

    with pytest.raises(GithubError) as caught:
        open_pull_request(report, files, client)

    assert caught.value.status_code == 503 and missing in caught.value.message
    assert calls == []


def test_token_does_not_show_in_repr_or_logs(caplog):
    report, files = approved_report()
    github = FakeGithub(fail={"POST /pulls": 422})
    caplog.set_level("DEBUG")

    with pytest.raises(GithubError) as caught:
        open_pull_request(report, files, github.client())

    assert "***" in caught.value.message, "GitHub lỡ trả token trong thông báo lỗi thì phải che"
    assert TOKEN not in caught.value.message
    assert TOKEN not in repr(github.client()) and TOKEN not in caplog.text
