from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ModuleNotFoundError:  # Pure json parsing should still be importable.
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]

try:
    from PySide6.QtCore import QObject, QTimer, Signal
except ModuleNotFoundError:  # Qt adapter is optional; parser/watcher can run headless.
    QObject = object  # type: ignore[assignment]
    QTimer = None  # type: ignore[assignment]
    Signal = None  # type: ignore[assignment]


SESSION_ROOT = Path.home() / ".codex" / "sessions"

StatusCallback = Callable[["PetStatusEvent"], None]


class CodexPetStatus(StrEnum):
    """Stable status values consumed by the Qt pet UI."""

    WAIT = "wait"
    RECEIPT = "receipt"
    THINKING = "thinking"
    WORKING = "working"
    REQUEST = "request"
    COMPLETE = "complete"
    ERROR = "error"
    NEWCHAT = "newchat"


INTERACTIVE_CALLS = {
    "request_plugin_install",
    "request_user_input",
    "request_approval",
    "request_permission",
}

ERROR_PAYLOAD_TYPES = {
    "error",
    "task_error",
    "exec_command_failed",
}


@dataclass(frozen=True)
class PetStatusEvent:
    status: str
    timestamp: str = ""
    record_type: str = ""
    payload_type: str = ""
    session_file: str = ""
    turn_id: str = ""
    call_id: str = ""
    function_name: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def infer_pet_status(payload: dict[str, Any]) -> CodexPetStatus | None:
    """Infer the pet animation state from a Codex jsonl payload."""
    payload_type = payload.get("type")
    function_name = str(payload.get("name") or "")

    if payload_type in ERROR_PAYLOAD_TYPES:
        return CodexPetStatus.ERROR

    if payload_type in {"task_started", "user_message"}:
        return CodexPetStatus.RECEIPT

    if payload_type == "reasoning":
        return CodexPetStatus.THINKING

    if payload_type == "agent_message":
        return CodexPetStatus.WORKING

    if payload_type in {"tool_search_call", "tool_search_output"}:
        return CodexPetStatus.WORKING

    if payload_type == "function_call":
        if function_name in INTERACTIVE_CALLS or function_name.startswith("request_"):
            return CodexPetStatus.REQUEST
        return CodexPetStatus.WORKING

    if payload_type == "function_call_output":
        if _payload_output_looks_like_error(payload):
            return CodexPetStatus.ERROR
        return CodexPetStatus.WORKING

    if payload_type == "task_complete":
        return CodexPetStatus.COMPLETE

    return None


def build_pet_status_event(
    record: dict[str, Any],
    session_file: Path | str = "",
    session_root: Path = SESSION_ROOT,
) -> PetStatusEvent | None:
    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        return None

    status = infer_pet_status(payload)
    if status is None:
        return None

    path_text = _relative_path_text(session_file, session_root)
    turn_id = _turn_id_from_payload(payload)

    return PetStatusEvent(
        status=status.value,
        timestamp=str(record.get("timestamp") or ""),
        record_type=str(record.get("type") or ""),
        payload_type=str(payload.get("type") or ""),
        session_file=path_text,
        turn_id=turn_id,
        call_id=str(payload.get("call_id") or ""),
        function_name=str(payload.get("name") or ""),
        message=_short_message(payload),
    )


def parse_jsonl_line(
    raw_line: bytes | str,
    session_file: Path | str = "",
    session_root: Path = SESSION_ROOT,
) -> PetStatusEvent | None:
    try:
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8")
        record = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(record, dict):
        return None
    return build_pet_status_event(record, session_file, session_root)


def parse_jsonl_lines(
    lines: Iterable[bytes | str],
    session_file: Path | str = "",
    session_root: Path = SESSION_ROOT,
) -> list[PetStatusEvent]:
    events = []
    for line in lines:
        event = parse_jsonl_line(line, session_file, session_root)
        if event is not None:
            events.append(event)
    return events


class PetStatusTailer:
    """Tail Codex rollout jsonl files and emit pet status events."""

    def __init__(
        self,
        root: Path = SESSION_ROOT,
        on_status: StatusCallback | None = None,
        emit_initial_wait: bool = True,
    ):
        self.root = root
        self.on_status = on_status
        self.offsets: dict[Path, int] = {}
        self.buffers: dict[Path, bytes] = {}
        self.lock = threading.Lock()
        self.latest: PetStatusEvent = PetStatusEvent(status=CodexPetStatus.WAIT.value)

        if emit_initial_wait:
            self._emit(self.latest)

    def initialize_existing_files(self) -> None:
        for path in self._session_files():
            try:
                self.offsets[path] = path.stat().st_size
                self.buffers[path] = b""
            except OSError as error:
                self._emit_error(path, error)

    def poll_files(self) -> None:
        for path in self._session_files():
            self.read_appended_content(path)

    def read_appended_content(self, path: Path) -> None:
        if not _is_rollout_jsonl(path):
            return

        if not path.exists():
            return

        with self.lock:
            try:
                current_size = path.stat().st_size
                offset = self.offsets.get(path, 0)

                if current_size < offset:
                    offset = 0
                    self.buffers[path] = b""

                with path.open("rb") as file:
                    file.seek(offset)
                    new_bytes = file.read()
                    self.offsets[path] = file.tell()
            except (OSError, PermissionError) as error:
                self._emit_error(path, error)
                return

            if not new_bytes:
                return

            data = self.buffers.get(path, b"") + new_bytes
            parts = data.split(b"\n")
            pending = parts.pop()
            self.buffers[path] = b""

            for raw_line in parts:
                self.parse_line(path, raw_line.rstrip(b"\r"))

            pending = pending.rstrip(b"\r")
            if pending and not self.parse_line(path, pending):
                self.buffers[path] = pending

    def parse_line(self, path: Path, raw_line: bytes) -> bool:
        if not raw_line:
            return True

        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False

        if not isinstance(record, dict):
            return True

        event = build_pet_status_event(record, path, self.root)
        if event is not None:
            self._emit(event)
        return True

    def _session_files(self) -> Iterable[Path]:
        return self.root.rglob("rollout-*.jsonl")

    def _emit(self, event: PetStatusEvent) -> None:
        self.latest = event
        if self.on_status is not None:
            self.on_status(event)

    def _emit_error(self, path: Path, error: BaseException) -> None:
        self._emit(
            PetStatusEvent(
                status=CodexPetStatus.ERROR.value,
                session_file=_relative_path_text(path, self.root),
                message=str(error),
            )
        )

    def emit_newchat(self, path: Path) -> None:
        if not _is_rollout_jsonl(path):
            return
        self._emit(
            PetStatusEvent(
                status=CodexPetStatus.NEWCHAT.value,
                session_file=_relative_path_text(path, self.root),
            )
        )


class CodexPetStatusHandler(FileSystemEventHandler):
    def __init__(self, tailer: PetStatusTailer):
        super().__init__()
        self.tailer = tailer

    def on_created(self, event: Any) -> None:
        if not event.is_directory:
            path = Path(event.src_path)
            self.tailer.emit_newchat(path)
            self.tailer.read_appended_content(path)

    def on_modified(self, event: Any) -> None:
        if not event.is_directory:
            self.tailer.read_appended_content(Path(event.src_path))

    def on_moved(self, event: Any) -> None:
        if not event.is_directory:
            path = Path(event.dest_path)
            self.tailer.emit_newchat(path)
            self.tailer.read_appended_content(path)


class CodexPetStatusWatcher:
    """Small wrapper that owns watchdog Observer lifecycle."""

    def __init__(
        self,
        root: Path = SESSION_ROOT,
        on_status: StatusCallback | None = None,
    ):
        if Observer is None:
            raise RuntimeError("watchdog is required to watch Codex session files")
        self.tailer = PetStatusTailer(root=root, on_status=on_status)
        self.observer = Observer()

    @property
    def latest(self) -> PetStatusEvent:
        return self.tailer.latest

    def start(self) -> None:
        if not self.tailer.root.exists():
            raise FileNotFoundError(f"Codex session directory not found: {self.tailer.root}")
        self.tailer.initialize_existing_files()
        self.observer.schedule(
            CodexPetStatusHandler(self.tailer),
            str(self.tailer.root),
            recursive=True,
        )
        self.observer.start()

    def stop(self) -> None:
        self.observer.stop()
        self.observer.join()

    def poll_once(self) -> PetStatusEvent:
        self.tailer.poll_files()
        return self.tailer.latest


if Signal is not None:

    class QtPetStatusSource(QObject):
        """Qt-friendly polling source that emits status_changed(dict)."""

        status_changed = Signal(dict)
        failed = Signal(str)

        def __init__(
            self,
            root: Path = SESSION_ROOT,
            interval_ms: int = 1000,
            parent: QObject | None = None,
        ):
            super().__init__(parent)
            self.tailer = PetStatusTailer(
                root=root,
                on_status=lambda event: self.status_changed.emit(event.to_dict()),
            )
            self.timer = QTimer(self)
            self.timer.setInterval(interval_ms)
            self.timer.timeout.connect(self.poll_once)
            self.observer = Observer() if Observer is not None else None
            self._observer_started = False

        def start(self) -> None:
            try:
                self.tailer.initialize_existing_files()
                if self.observer is not None and not self._observer_started:
                    self.observer.schedule(
                        CodexPetStatusHandler(self.tailer),
                        str(self.tailer.root),
                        recursive=True,
                    )
                    self.observer.start()
                    self._observer_started = True
            except Exception as error:  # noqa: BLE001 - surface to UI.
                self.failed.emit(str(error))
                return
            if not self.timer.isActive():
                self.timer.start()

        def stop(self) -> None:
            self.timer.stop()
            if self.observer is not None and self._observer_started:
                self.observer.stop()
                self.observer.join()
                self._observer_started = False

        def poll_once(self) -> None:
            try:
                self.tailer.poll_files()
            except Exception as error:  # noqa: BLE001 - surface to UI.
                self.failed.emit(str(error))

        def latest_status(self) -> dict[str, Any]:
            return self.tailer.latest.to_dict()

else:
    QtPetStatusSource = None  # type: ignore[assignment]


def print_status_event(event: PetStatusEvent) -> None:
    print(event.to_json(), flush=True)


def main() -> int:
    watcher = CodexPetStatusWatcher(on_status=print_status_event)
    watcher.start()
    try:
        while True:
            watcher.poll_once()
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
    return 0


def _is_rollout_jsonl(path: Path) -> bool:
    return path.name.startswith("rollout-") and path.suffix.lower() == ".jsonl"


def _turn_id_from_payload(payload: dict[str, Any]) -> str:
    turn_id = payload.get("turn_id")
    if turn_id:
        return str(turn_id)

    metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
    if isinstance(metadata, dict):
        return str(metadata.get("turn_id") or "")
    return ""


def _relative_path_text(path: Path | str, root: Path) -> str:
    if not path:
        return ""

    path = Path(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _short_message(payload: dict[str, Any]) -> str:
    for key in ("message", "error", "status", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:160]
    return ""


def _payload_output_looks_like_error(payload: dict[str, Any]) -> bool:
    output = payload.get("output")
    if isinstance(output, dict):
        return bool(output.get("error") or output.get("is_error"))
    if isinstance(output, str):
        lowered = output.lower()
        return "error" in lowered or "traceback" in lowered or "failed" in lowered
    return False


if __name__ == "__main__":
    raise SystemExit(main())
