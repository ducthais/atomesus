"""Service deployer: kéo nhánh mặc định của repo pipeline, kiểm manifest, cài vào Dagster production.

Chạy trong container `deployer` của deploy/compose.yml (xem docs/deploy.md, mục bật deployer). Mỗi
DEPLOYER_INTERVAL_SEC giây (mặc định 60) chạy một chu kỳ src/deployer/service.py; lỗi một chu kỳ (mạng, git) chỉ ghi
log, chu kỳ sau thử lại. Chưa cấu hình (thiếu biến môi trường) thì vòng lặp ghi lỗi rồi nằm chờ thay vì thoát, để
container không khởi động lại liên tục; workspace.yaml rỗng vẫn có để Dagster chạy với danh sách pipeline rỗng.

Chạy: python scripts/deployer.py            (vòng lặp)
      python scripts/deployer.py --once     (một chu kỳ rồi thoát; mã 1 nếu lỗi, còn kết quả chưa báo hoặc còn
                                             pipeline chưa cài xong)
      python scripts/deployer.py --once --reinstall   (cài lại mọi pipeline ở commit mới nhất)
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # đường dẫn mặc định (data/dagster_local, data/deployer) tính từ gốc repo như scripts/dagster_local.py

from src.deployer.install import write_workspace  # noqa: E402
from src.deployer.service import Config, DeployerError, run_once  # noqa: E402

log = logging.getLogger("deployer")
IDLE_SEC = 3600


def cycle(config: Config, reinstall: bool) -> dict | None:
    """Một chu kỳ; trả tóm tắt của run_once, None nếu chu kỳ lỗi."""
    try:
        summary = run_once(config, reinstall=reinstall)
    except DeployerError as exc:
        log.error("%s", exc)
        return None
    except Exception as exc:  # không để một chu kỳ lỗi làm tắt deployer
        log.error(
            "Chu kỳ deployer lỗi (%s); kiểm tra cấu hình và quyền volume, chu kỳ sau thử lại.", type(exc).__name__
        )
        return None
    if summary["models"] or summary["pending"]:
        log.info(
            "Commit %s: kiểm/cài %s, còn %d kết quả chưa báo, pipeline chưa xong %s.",
            (summary["head"] or "?")[:12],
            summary["models"],
            summary["pending"],
            summary["unresolved"],
        )
    return summary


def idle(error: DeployerError) -> NoReturn:
    """Chưa cấu hình: Dagster vẫn cần workspace.yaml (rỗng) trong volume code location, rồi nằm chờ người sửa .env."""
    deploy_dir = Path(os.environ.get("PIPELINE_DEPLOY_DIR", "data/dagster_local"))
    if not (deploy_dir / "workspace.yaml").exists():
        write_workspace(str(deploy_dir))
    while True:
        log.error("Deployer chưa bật: %s Xem docs/deploy.md mục 6, sửa .env rồi `up -d deployer`.", error)
        time.sleep(IDLE_SEC)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="chạy một chu kỳ rồi thoát")
    parser.add_argument("--reinstall", action="store_true", help="cài lại mọi pipeline ở commit mới nhất")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    try:
        config = Config.from_env()
    except DeployerError as exc:
        if args.once:
            log.error("%s", exc)
            return 2
        idle(exc)
    log.info("Deployer chạy: chu kỳ %ds, code location ở %s.", config.interval, config.deploy_dir)
    if args.once:
        summary = cycle(config, args.reinstall)
        return 0 if summary and not summary["pending"] and not summary["unresolved"] else 1
    reinstall = args.reinstall
    while True:
        summary = cycle(config, reinstall)
        if summary and summary["head"]:  # đã cài lại (dù còn kết quả chờ báo): không cài lại lần nữa
            reinstall = False
        time.sleep(config.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
