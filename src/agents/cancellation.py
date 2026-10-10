"""Tín hiệu dừng của phase đang chạy (src/agents/timeout.py): quá hạn hoặc người dùng hủy run.

Mỗi phase chạy trong luồng riêng và gắn một Event vào luồng đó; code chạy lâu (sandbox, gọi LLM) hỏi tín hiệu này
để dừng công việc còn lại thay vì chạy tiếp sau khi graph đã bỏ phase.
"""

import threading

_local = threading.local()


def bind_phase_cancel(event: threading.Event) -> None:
    _local.phase_cancel = event


def raise_if_phase_cancelled() -> None:
    event = getattr(_local, "phase_cancel", None)
    if event is not None and event.is_set():
        raise TimeoutError("Phase đã bị dừng (quá hạn hoặc run bị hủy); bỏ công việc còn lại.")
