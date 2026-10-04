"""Điền mật khẩu Postgres nguồn vào `.env` gốc cho máy local, không ghi đè giá trị đã có.

- Chưa có `.env`: tạo từ `.env.example`.
- Còn file cũ `infra/postgres/.env` (trước khi gộp về một `.env`): lấy lại mật khẩu từ đó để khớp volume Postgres đang
  chạy, rồi có thể xóa file cũ.
- Thiếu hoặc còn placeholder `replace-...`: sinh mật khẩu ngẫu nhiên.
"""

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV, EXAMPLE = ROOT / ".env", ROOT / ".env.example"
LEGACY = ROOT / "infra/postgres/.env"
KEYS = ("POSTGRES_PASSWORD", "PIPELINE_READER_PASSWORD", "PIPELINE_DATABASE_URL")


def parse(lines: list[str]) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))
    return {key.strip(): value.strip() for key, value in pairs}


def usable(value: str | None) -> bool:
    return bool(value) and "replace-" not in value


def main():
    created = not ENV.exists()
    template = EXAMPLE if created else ENV
    lines = template.read_text(encoding="utf-8").splitlines() if template.exists() else []
    current = parse(lines)
    if all(usable(current.get(key)) for key in KEYS):
        print(".env đã có mật khẩu Postgres nguồn; giữ nguyên.")
        return
    legacy = parse(LEGACY.read_text(encoding="utf-8").splitlines()) if LEGACY.exists() else {}

    def pick(key: str) -> str | None:
        return next((v for v in (current.get(key), legacy.get(key)) if usable(v)), None)

    reader = pick("PIPELINE_READER_PASSWORD") or secrets.token_urlsafe(24)
    values = {
        "POSTGRES_PASSWORD": pick("POSTGRES_PASSWORD") or secrets.token_urlsafe(24),
        "PIPELINE_READER_PASSWORD": reader,
        "PIPELINE_DATABASE_URL": pick("PIPELINE_DATABASE_URL")
        or f"postgresql://pipeline_reader:{reader}@127.0.0.1:5433/pipeline_demo",
    }
    out, seen = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    missing = [key for key in KEYS if key not in seen]
    if missing:
        out += ["", "# ---- Postgres nguồn ----", *(f"{key}={values[key]}" for key in missing)]
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    if created:
        ENV.chmod(0o600)
    source = f"lấy lại từ {LEGACY.relative_to(ROOT).as_posix()}" if legacy else "mật khẩu ngẫu nhiên"
    print(f"{'Tạo' if created else 'Cập nhật'} .env: Postgres nguồn ({source}). File bị Git bỏ qua.")
    if legacy:
        print(f"Có thể xóa {LEGACY.relative_to(ROOT).as_posix()}: mọi lệnh giờ dùng --env-file .env.")


if __name__ == "__main__":
    main()
