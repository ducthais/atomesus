import threading

_local = threading.local()


def bind_phase_cancel(event: threading.Event) -> None:
    _local.phase_cancel = event


def raise_if_phase_cancelled() -> None:
    event = getattr(_local, "phase_cancel", None)
    if event is not None and event.is_set():
        raise TimeoutError("Phase đã quá hạn và yêu cầu dừng công việc còn lại.")
