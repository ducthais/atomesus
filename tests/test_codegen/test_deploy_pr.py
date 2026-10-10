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
SYMLINK, EXECUTABLE, SUBMODULE = "120000", "100755", "160000"


def _entry(value: str | dict | None) -> dict | None:
    """Entry git của một giá trị trong cây giả: chuỗi là file thường 100644; dict ghi rõ mode (symlink, file thực thi:
    `content`; submodule: `sha` commit)."""
    if value is None:
        return None
    if isinstance(value, str):
        return {"type": "blob", "mode": "100644", "sha": blob_sha(value)}
    if value["mode"] == SUBMODULE:
        return {"type": "commit", "mode": SUBMODULE, "sha": value["sha"]}
    return {"type": "blob", "mode": value["mode"], "sha": blob_sha(value["content"])}


def _text(value: str | dict) -> str | None:
    return value if isinstance(value, str) else value.get("content")


class FakeGithub:
    """GitHub giả: đủ các API mà GithubClient dùng, lưu repo trong bộ nhớ và ghi lại mọi request.

    `fail`: {"METHOD đường-dẫn-đầu": mã HTTP hoặc "timeout"} để giả lập lỗi. `race_pull`: POST /pulls tạo pull request
    rồi trả 422, như khi một request Deploy khác vừa mở trước. `race_ref(sha commit vừa tạo) -> sha`: POST /git/refs
    thấy request khác vừa tạo nhánh trỏ vào commit trả về rồi trả 422. `before`: {"METHOD đường-dẫn-đầu": hàm} chạy
    trước khi xử lý request khớp, như khi có người đẩy commit lên nhánh đúng lúc đó. Đầu pull request luôn là đầu nhánh
    hiện tại, như trên GitHub. Cây lưu {đường dẫn: nội dung}; symlink, file thực thi, submodule ghi dạng dict (_entry).
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
        self.before: dict = {}

    def client(self) -> GithubClient:
        return GithubClient(REPO, SecretStr(TOKEN), transport=httpx.MockTransport(self.handle))

    def files_at(self, branch: str) -> dict[str, str]:
        return self.trees[self.commits[self.refs[branch]]["tree"]]

    def writes(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if r[0] != "GET"]

    def push(self, branch: str, path: str, content: str | dict | None) -> str:
        """Commit đẩy thẳng lên nhánh (ngoài hệ thống), trả sha mới. `content` None là xóa file."""
        tree = {**self.files_at(branch), path: content}
        if content is None:
            del tree[path]
        tree_sha, sha = f"t_push{len(self.trees)}", f"c_push{len(self.commits)}"
        self.trees[tree_sha] = tree
        self.commits[sha] = {"tree": tree_sha, "parents": [self.refs[branch]], "message": "đẩy thẳng"}
        self.refs[branch] = sha
        return sha

    def _ancestors(self, sha: str) -> list[str]:
        """Commit `sha` và mọi commit cha, gần trước xa sau."""
        seen, queue = [], [sha]
        while queue:
            current = queue.pop(0)
            if current not in seen:
                seen.append(current)
                queue += self.commits[current]["parents"]
        return seen

    def _compare(self, base: str, head: str) -> dict:
        """Như GitHub compare `base...head`: file thay đổi từ điểm rẽ nhánh (merge base) tới `head`."""
        base, head = self.refs.get(base, base), self.refs.get(head, head)
        merge_base = next(sha for sha in self._ancestors(head) if sha in self._ancestors(base))
        old, new = self.trees[self.commits[merge_base]["tree"]], self.trees[self.commits[head]["tree"]]
        files = []
        for path in sorted(set(old) | set(new)):
            if path not in new:
                files.append({"filename": path, "status": "removed", "sha": _entry(old[path])["sha"]})
            elif _entry(old.get(path)) != _entry(new[path]):
                status = "modified" if path in old else "added"
                files.append({"filename": path, "status": status, "sha": _entry(new[path])["sha"]})
        return {"merge_base_commit": {"sha": merge_base}, "files": files}

    def _current(self, pull: dict) -> dict:
        return {**pull, "head": {**pull["head"], "sha": self.refs[pull["head"]["ref"]]}}

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
        for key, hook in self.before.items():
            if f"{method} {path}".startswith(key):
                hook()
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
            # Như GitHub với recursive=1: có cả entry thư mục (type tree, mode 040000).
            folders = {p.rsplit("/", i)[0] for p in tree for i in range(1, p.count("/") + 1)}
            entries = [{"path": p, "type": "tree", "mode": "040000", "sha": f"tree:{p}"} for p in sorted(folders)]
            entries += [{"path": p, **_entry(value)} for p, value in tree.items()]
            return httpx.Response(200, json={"tree": entries, "truncated": False})
        if method == "GET" and path.startswith("/git/blobs/"):
            sha = path.removeprefix("/git/blobs/")
            contents = [_text(value) for tree in self.trees.values() for value in tree.values()]
            content = next(c for c in contents if c is not None and blob_sha(c) == sha)
            encoded = base64.b64encode(content.encode()).decode()
            return httpx.Response(200, json={"sha": sha, "content": encoded, "encoding": "base64"})
        if method == "GET" and path.startswith("/compare/"):
            base, head = path.removeprefix("/compare/").split("...", 1)
            return httpx.Response(200, json=self._compare(base, head))
        if method == "POST" and path == "/git/trees":
            tree = dict(self.trees[body["base_tree"]])
            for entry in body["tree"]:
                if "content" in entry:
                    tree[entry["path"]] = entry["content"]
                elif entry.get("sha") is None:
                    old = _entry(tree.get(entry["path"]))
                    assert old is not None, f"xóa entry không tồn tại: {entry['path']}"
                    assert (entry["mode"], entry["type"]) == (old["mode"], old["type"]), "xóa phải đúng mode/type"
                    tree.pop(entry["path"])
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
                200, json=[self._current(p) for p in self.pulls if p["head"]["ref"] == branch and p["state"] == "open"]
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

    branch = branch_name("fct_daily_orders", report.run_id, report.version)
    assert branch == "pipeline/fct_daily_orders/3f6c1a2e-v3"
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
    branch = branch_name("fct_daily_orders", report.run_id, report.version)
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
    """Run khác trùng nhánh (cùng model, version và 8 ký tự đầu run_id): cùng bộ file nhưng khác run_id, hoặc cùng
    run_id nhưng bộ file khác."""
    report, files = approved_report()
    changed = {**files, next(iter(files)): files[next(iter(files))] + "\n-- bản khác\n"}
    return [
        (report, files, signed_copy(report, files, run_id="3f6c1a2e-0000-4000-8000-000000000002"), files),
        (report, files, signed_copy(report, changed), changed),
    ]


def test_two_runs_of_same_model_and_version_open_separate_pull_requests():
    report, files = approved_report()
    changed = {**files, next(iter(files)): files[next(iter(files))] + "\n-- bản khác\n"}
    other = signed_copy(report, changed, run_id="9d1e2f3a-0000-4000-8000-000000000002")
    github = FakeGithub()

    first = open_pull_request(report, files, github.client())
    second = open_pull_request(other, changed, github.client())

    assert first.branch == "pipeline/fct_daily_orders/3f6c1a2e-v3"
    assert second.branch == "pipeline/fct_daily_orders/9d1e2f3a-v3"
    assert (first.pr_number, second.pr_number) == (1, 2) and len(github.pulls) == 2
    for deployment, run, run_files in ((first, report, files), (second, other, changed)):
        pull = github.pulls[deployment.pr_number - 1]
        assert pull["head"]["ref"] == deployment.branch and run.run_id in pull["body"]
        tree = github.files_at(deployment.branch)
        assert {p: tree[f"{FOLDER}/{p}"] for p in run_files} == run_files
        assert json.loads(tree[f"{FOLDER}/manifest.json"])["run_id"] == run.run_id


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
    github.refs[branch_name("fct_daily_orders", report.run_id, report.version)] = "c0"

    with pytest.raises(ManifestError):
        open_pull_request(report, files, github.client())

    assert github.writes() == [] and github.pulls == []


def test_pull_request_changed_after_opening_is_not_returned():
    report, files = approved_report()
    github = FakeGithub()
    open_pull_request(report, files, github.client())
    branch = branch_name("fct_daily_orders", report.run_id, report.version)
    tree = dict(github.files_at(branch))
    tree[f"{FOLDER}/{next(iter(files))}"] = "-- sửa thẳng trên nhánh\n"
    github.trees["t_sua"] = tree
    github.commits["c_sua"] = {"tree": "t_sua", "parents": [github.refs[branch]], "message": "sửa"}
    github.refs[branch] = "c_sua"

    with pytest.raises(ManifestError):
        open_pull_request(report, files, github.client())


def unsigned_change(files: dict[str, str], change: str) -> tuple[str, str | dict | None]:
    """Thay đổi ngoài chữ ký đẩy lên nhánh: (đường dẫn, nội dung mới; None là xóa)."""
    first = next(iter(files))
    return {
        # File đã ký đổi thành symlink / file thực thi nhưng giữ nguyên nội dung (sha blob không đổi).
        "symlink": (f"{FOLDER}/{first}", {"mode": SYMLINK, "content": files[first]}),
        "thuc_thi": (f"{FOLDER}/{first}", {"mode": EXECUTABLE, "content": files[first]}),
        "submodule": (f"{FOLDER}/vendor", {"mode": SUBMODULE, "sha": "a" * 40}),
        "them_ngoai_thu_muc": (".github/workflows/deploy.yml", "on: push\njobs: {}\n"),
        "xoa_ngoai_thu_muc": ("README.md", None),
        "thua_trong_thu_muc": (f"{FOLDER}/extra.sql", "select 1\n"),
    }[change]


@pytest.mark.parametrize("state", ["pr_open", "branch_only"])
@pytest.mark.parametrize(
    "change",
    ["symlink", "thuc_thi", "submodule", "them_ngoai_thu_muc", "xoa_ngoai_thu_muc", "thua_trong_thu_muc"],
)
def test_existing_branch_with_unsigned_change_is_rejected(change, state):
    report, files = approved_report()
    github = FakeGithub()
    if state == "branch_only":
        github.fail = {"POST /pulls": 500}
        with pytest.raises(GithubError):
            open_pull_request(report, files, github.client())
        github.fail = {}
    else:
        open_pull_request(report, files, github.client())
    github.push(branch_name("fct_daily_orders", report.run_id, report.version), *unsigned_change(files, change))
    writes, pulls = len(github.writes()), len(github.pulls)

    with pytest.raises(ManifestError) as caught:
        open_pull_request(report, files, github.client())

    assert "khác bộ file đã duyệt" in str(caught.value)
    assert len(github.writes()) == writes and len(github.pulls) == pulls, "không mở hay trả pull request"


def test_existing_branch_is_compared_from_where_it_left_default_branch():
    """Nhánh mặc định đi tiếp sau khi mở nhánh, file cũ không còn ký đã bị xóa: phần diff vẫn đúng bộ file đã ký."""
    report, files = approved_report()
    github = FakeGithub()
    github.trees["t0"] = {"README.md": "x\n", f"{FOLDER}/old_model.sql": "select 1\n"}
    first = open_pull_request(report, files, github.client())
    github.push("main", "README.md", "sửa trên nhánh mặc định\n")

    again = open_pull_request(report, files, github.client())

    assert again.pr_number == first.pr_number and len(github.pulls) == 1
    assert ("GET", f"/compare/main...{first.head_sha}") in github.requests


@pytest.mark.parametrize(
    ("setup", "moment"),
    [
        ("pr_open", "GET /pulls"),  # trả pull request đã mở
        ("new_branch", "POST /pulls"),  # pull request vừa tạo trên nhánh vừa commit
        ("branch_only", "POST /pulls"),  # pull request vừa tạo trên nhánh có sẵn đã kiểm
        ("race_pull", "GET /pulls"),  # phục hồi sau 422: pull request request khác vừa mở
    ],
)
def test_branch_moved_between_check_and_pull_request_is_rejected(setup, moment):
    report, files = approved_report()
    github = FakeGithub(race_pull=setup == "race_pull")
    branch = branch_name("fct_daily_orders", report.run_id, report.version)
    if setup == "pr_open":
        open_pull_request(report, files, github.client())
    if setup == "branch_only":
        github.fail = {"POST /pulls": 500}
        with pytest.raises(GithubError):
            open_pull_request(report, files, github.client())
        github.fail = {}
    github.before = {moment: lambda: github.push(branch, f"{FOLDER}/{next(iter(files))}", "-- đẩy thẳng\n")}

    with pytest.raises(ManifestError) as caught:
        open_pull_request(report, files, github.client())

    assert "vừa bị đổi" in str(caught.value)
    assert github.refs[branch].startswith("c_push"), "nhánh đã bị đổi đúng lúc lấy/tạo pull request"


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
    branch = branch_name("fct_daily_orders", first.run_id, first.version)
    assert branch == branch_name("fct_daily_orders", second.run_id, second.version)
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

    tree = github.files_at(branch_name("fct_daily_orders", report.run_id, report.version))
    assert f"{FOLDER}/old_model.sql" not in tree
    assert tree["pipelines/other/a.sql"] == "a\n", "không đụng pipeline khác"


def test_stale_submodule_and_symlink_in_folder_are_removed():
    report, files = approved_report()
    github = FakeGithub()
    github.trees["t0"] = {
        "README.md": "x\n",
        f"{FOLDER}/vendor": {"mode": SUBMODULE, "sha": "a" * 40},
        f"{FOLDER}/link.sql": {"mode": SYMLINK, "content": "../../secrets.sql"},
    }

    first = open_pull_request(report, files, github.client())

    tree = github.files_at(first.branch)
    assert f"{FOLDER}/vendor" not in tree and f"{FOLDER}/link.sql" not in tree
    in_folder = {p for p in tree if p.startswith(f"{FOLDER}/")}
    assert in_folder == {f"{FOLDER}/{p}" for p in [*files, "run_report.json", "manifest.json"]}
    again = open_pull_request(report, files, github.client())
    assert again.pr_number == first.pr_number, "Deploy lại cùng bản vẫn nhận đúng nhánh vừa tạo"


@pytest.mark.parametrize("moment", ["existing", "race"])
def test_pull_request_targeting_other_branch_is_rejected(moment):
    report, files = approved_report()
    github = FakeGithub()
    if moment == "existing":
        open_pull_request(report, files, github.client())
        github.pulls[0]["base"]["ref"] = "release-other"
    else:
        # Request khác vừa mở pull request của nhánh này nhưng vào nhánh khác (POST /pulls trả 422).
        github.race_pull = True
        original = github._pull
        github._pull = lambda head, base, title, body: original(head, "release-other", title, body)

    with pytest.raises(ManifestError, match="release-other"):
        open_pull_request(report, files, github.client())


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
