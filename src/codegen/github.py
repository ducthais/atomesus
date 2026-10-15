"""Client GitHub tối thiểu cho P16 (SPEC §9.5): mở pull request vào repo pipeline, không bao giờ merge.

Chỉ gọi đúng các API cần: đọc repo, ref, commit, tree, blob và compare; tạo tree / commit / ref; liệt kê và tạo
pull request (bất biến 8, SPEC §19). Token là fine-grained PAT chỉ cấp cho repo pipeline (Contents RW, Pull requests
RW, Metadata R); chốt chặn merge thật là branch protection của repo đó.

Cấu hình nằm riêng ở đây, không nằm trong PipelineSettings: PipelineSettings được đưa vào agent và sandbox, token
GitHub không đi theo. Token không bao giờ ghi vào log, response, RunReport hay error_trace.
"""

import base64
import hashlib
import logging
import re
from typing import Any

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
DETAIL_LIMIT = 300
# GitHub compare chỉ liệt kê tối đa 300 file thay đổi.
COMPARE_FILE_LIMIT = 300
REGULAR_FILE = "100644"


class GithubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_GITHUB_", env_file=".env", extra="ignore")
    # Fine-grained PAT chỉ cấp cho repo pipeline; để trống thì Deploy báo chưa cấu hình (503).
    token: SecretStr = SecretStr("")
    # Repo pipeline dạng owner/tên-repo, ví dụ acme/atomesus-pipelines.
    repo: str = ""
    # GitHub Enterprise Server thì đổi thành https://<host>/api/v3.
    api_url: str = "https://api.github.com"
    timeout_sec: float = Field(default=15, gt=0, le=120)
    # Secret của webhook repo pipeline (PIPELINE_GITHUB_WEBHOOK_SECRET); để trống thì /webhooks/github trả 503.
    webhook_secret: SecretStr = SecretStr("")


class GithubError(Exception):
    """Gọi GitHub thất bại. `message` tiếng Việt đã an toàn để trả người dùng và ghi error_trace.

    `status_code`: mã trả cho client của backend (502 / 503). `upstream`: mã HTTP GitHub trả, None nếu không gọi được.
    """

    def __init__(self, status_code: int, message: str, upstream: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.upstream = upstream


def blob_sha(content: str) -> str:
    """sha git của một file, như GitHub tính khi ghi `content`: so được file trên nhánh mà không cần tải về."""
    data = content.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _detail(response: httpx.Response, token: str) -> str:
    """Thông báo lỗi GitHub trả về, cắt ngắn và che token nếu lỡ có."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    parts = [str(body.get("message") or "")]
    parts += [str(e.get("message") or e.get("code") or "") for e in body.get("errors") or [] if isinstance(e, dict)]
    text = "; ".join(p for p in parts if p)
    if token:
        text = text.replace(token, "***")
    return text[:DETAIL_LIMIT]


def _http_error(status: int, detail: str, repo: str) -> GithubError:
    suffix = f": {detail}" if detail else ""
    if status == 401:
        message = "GitHub từ chối token (401): PIPELINE_GITHUB_TOKEN sai, đã hết hạn hoặc đã bị thu hồi."
    elif status == 403:
        message = (
            f"Token không đủ quyền trên repo {repo} hoặc đã chạm giới hạn tần suất của GitHub (403). "
            "Token cần quyền Contents và Pull requests (đọc/ghi) trên repo pipeline."
        )
    elif status == 404:
        message = (
            f"Không thấy repo {repo} hoặc dữ liệu cần dùng trên đó (404): kiểm tra PIPELINE_GITHUB_REPO và "
            "repo đã được cấp cho token chưa."
        )
    elif status == 409:
        message = f"Repo {repo} đang trống hoặc xung đột (409); tạo commit đầu tiên cho nhánh mặc định rồi Deploy lại."
    elif status == 422:
        message = f"GitHub từ chối yêu cầu (422){suffix}"
    elif status >= 500:
        return GithubError(503, f"GitHub đang lỗi ({status}), thử Deploy lại sau ít phút.", status)
    else:
        message = f"GitHub trả mã {status} ngoài dự kiến{suffix}"
    return GithubError(502, message, status)


class GithubClient:
    """Thao tác trên đúng một repo pipeline. `transport` để test thay GitHub bằng transport giả, không ra mạng."""

    def __init__(
        self,
        repo: str,
        token: SecretStr,
        api_url: str = "https://api.github.com",
        timeout_sec: float = 15,
        transport: httpx.BaseTransport | None = None,
    ):
        self.repo = repo.strip()
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout_sec
        self._transport = transport

    @classmethod
    def from_settings(cls, config: GithubSettings | None = None) -> "GithubClient":
        config = config or GithubSettings()
        return cls(config.repo, config.token, config.api_url, config.timeout_sec)

    def __repr__(self) -> str:
        return f"GithubClient(repo={self.repo!r})"

    @property
    def configured(self) -> bool:
        """Whether optional GitHub operations have both required settings, without exposing the token."""
        return bool(self.repo.strip() and self._token.get_secret_value().strip())

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]

    def _check_config(self) -> str:
        token = self._token.get_secret_value().strip()
        missing = [
            name for name, value in (("PIPELINE_GITHUB_TOKEN", token), ("PIPELINE_GITHUB_REPO", self.repo)) if not value
        ]
        if missing:
            raise GithubError(503, f"Chưa cấu hình {' và '.join(missing)} ở backend nên chưa mở được pull request.")
        if not REPO_PATTERN.match(self.repo):
            raise GithubError(503, "PIPELINE_GITHUB_REPO phải có dạng owner/tên-repo.")
        return token

    def _call(self, method: str, path: str, *, json: Any = None, params: dict | None = None, missing_ok=False) -> Any:
        token = self._check_config()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "atomesus-deploy",
        }
        # Không theo redirect: token không bao giờ bị gửi sang host khác.
        try:
            with httpx.Client(
                base_url=self._api_url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as http:
                response = http.request(method, f"/repos/{self.repo}{path}", json=json, params=params)
        except httpx.TimeoutException:
            log.warning("GitHub %s %s: hết thời gian chờ", method, path)
            raise GithubError(503, f"GitHub không trả lời trong {self._timeout:g} giây, thử Deploy lại sau.") from None
        except httpx.HTTPError as exc:
            log.warning("GitHub %s %s: %s", method, path, type(exc).__name__)
            raise GithubError(503, f"Không kết nối được GitHub ({type(exc).__name__}), thử Deploy lại sau.") from None
        if missing_ok and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            log.warning("GitHub %s %s: HTTP %s", method, path, response.status_code)
            raise _http_error(response.status_code, _detail(response, token), self.repo)
        try:
            return response.json()
        except ValueError:
            raise GithubError(502, "GitHub trả nội dung không phải JSON.", response.status_code) from None

    def repository(self) -> dict:
        """Thông tin repo: `default_branch`, `html_url`."""
        return self._call("GET", "")

    def ref_sha(self, branch: str) -> str | None:
        """sha commit đầu nhánh, None nếu nhánh chưa có."""
        found = self._call("GET", f"/git/ref/heads/{branch}", missing_ok=True)
        return found["object"]["sha"] if found else None

    def commit_tree(self, commit_sha: str) -> str:
        return self._call("GET", f"/git/commits/{commit_sha}")["tree"]["sha"]

    def tree_entries(self, tree_sha: str) -> dict[str, dict]:
        """Mọi entry không phải thư mục trong cây (đệ quy): đường dẫn → {type, mode, sha}.

        `type` blob (file thường 100644, thực thi 100755, symlink 120000) hoặc commit (submodule 160000).
        """
        tree = self._call("GET", f"/git/trees/{tree_sha}", params={"recursive": "1"})
        if tree.get("truncated"):
            raise GithubError(502, "Cây thư mục repo pipeline quá lớn để GitHub trả đủ; tách bớt rồi Deploy lại.")
        return {
            item["path"]: {"type": item["type"], "mode": item["mode"], "sha": item["sha"]}
            for item in tree["tree"]
            if item["type"] != "tree"
        }

    def compare_files(self, base: str, head: str) -> list[str]:
        """Đường dẫn mọi file `head` thay đổi so với điểm rẽ nhánh từ `base` (đúng phần diff của pull request).

        File đổi tên tính cả tên cũ. GitHub trả tối đa COMPARE_FILE_LIMIT file; chạm trần thì danh sách có thể thiếu.
        """
        compared = self._call("GET", f"/compare/{base}...{head}")
        paths = []
        for item in compared["files"]:
            paths.append(item["filename"])
            if item.get("previous_filename"):
                paths.append(item["previous_filename"])
        return paths

    def blob_text(self, blob_sha: str) -> str:
        """Nội dung một file (UTF-8) theo sha blob."""
        blob = self._call("GET", f"/git/blobs/{blob_sha}")
        if blob.get("encoding") != "base64":
            raise GithubError(502, "GitHub trả nội dung file không ở dạng base64.")
        try:
            return base64.b64decode(blob["content"]).decode()
        except ValueError:
            raise GithubError(502, "Nội dung file trên repo pipeline không đọc được (không phải UTF-8).") from None

    def create_tree(self, base_tree: str, files: dict[str, str], delete: dict[str, dict] | None = None) -> str:
        """Cây mới = `base_tree` + `files` (ghi đè), bỏ các entry trong `delete` (đường dẫn → entry của tree_entries).

        Xóa giữ đúng mode/type của entry cũ để bỏ được cả symlink, file thực thi và submodule, không chỉ file thường.
        """
        entries = [{"path": p, "mode": REGULAR_FILE, "type": "blob", "content": c} for p, c in sorted(files.items())]
        entries += [
            {"path": p, "mode": entry["mode"], "type": entry["type"], "sha": None}
            for p, entry in sorted((delete or {}).items())
        ]
        return self._call("POST", "/git/trees", json={"base_tree": base_tree, "tree": entries})["sha"]

    def create_commit(self, message: str, tree_sha: str, parents: list[str]) -> str:
        return self._call("POST", "/git/commits", json={"message": message, "tree": tree_sha, "parents": parents})[
            "sha"
        ]

    def create_ref(self, branch: str, sha: str) -> None:
        self._call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    def open_pull_requests(self, branch: str) -> list[dict]:
        """Pull request đang mở có head là `branch` của chính repo này."""
        return self._call("GET", "/pulls", params={"head": f"{self.owner}:{branch}", "state": "open"})

    def create_pull_request(self, *, title: str, body: str, head: str, base: str) -> dict:
        return self._call("POST", "/pulls", json={"title": title, "body": body, "head": head, "base": base})
