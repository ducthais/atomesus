"""Runtime của code location Dagster hợp nhất (lỗi #4) và credential theo từng pipeline (lỗi N1).

- `loader`: code location duy nhất `LOCATION`, nạp mọi bundle template v2 (`RUNTIME_CONTRACT`) bằng Definitions.merge.
- `contract`: đọc `RUNTIME_CONTRACT` của bundle bằng AST, không thực thi file.
- `secrets`: supervisor (scripts/dagster_local.py, có khóa giải mã) ghi credential của đúng các nguồn mỗi pipeline dùng
  vào `<ATOMESUS_SECRETS_DIR>/<model>.json`.
- `execute`: thân op của bundle v2 chạy dlt bằng subprocess với env chỉ gồm credential và file DuckDB của pipeline đó.

Module này không import gì nặng: bundle và loader import nó lúc code server nạp.
"""

LOCATION = "atomesus"
GENERATION_FILE = ".generation"  # deployer ghi sau mỗi lần cài / rollback; supervisor thấy đổi thì reload
RUNTIME_CONTRACT = 2
SECRETS_DIR_ENV = "ATOMESUS_SECRETS_DIR"
DEPLOY_DIR_ENV = "PIPELINE_DEPLOY_DIR"
WAREHOUSE_DIR_ENV = "WAREHOUSE_DIR"
