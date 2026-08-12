from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VSCODE_EXTENSIONS_DIR = Path.home() / ".vscode" / "extensions"

MISSING = object()
SOURCE_PRIORITY = ("codexcli", "vscode")


@dataclass
class AppServerStats:
    source: str
    command: list[str]
    started_at: float = field(default_factory=time.time)
    requests: int = 0
    responses: int = 0
    notifications: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "command": self.command,
            "requests": self.requests,
            "responses": self.responses,
            "notifications": self.notifications,
            "bytesSent": self.bytes_sent,
            "bytesReceived": self.bytes_received,
            "uptimeMs": int((time.time() - self.started_at) * 1000),
        }


class CodexAppServer:
    def __init__(self, source: str, codex_exe: str):
        self.command = [codex_exe, "app-server"]
        self.stats = AppServerStats(source=source, command=self.command)
        self.proc = subprocess.Popen(
            self.command,
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._next_id = 1

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=3)
        finally:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    stream.close()

    def request(
        self,
        method: str,
        params: Any = MISSING,
        timeout: float = 15,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1

        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not MISSING:
            message["params"] = params

        self.stats.requests += 1
        self._write_json_line(message)

        deadline = time.time() + timeout
        while time.time() < deadline:
            incoming = self._read_json_line()
            if "id" not in incoming:
                self.stats.notifications += 1
                continue

            self.stats.responses += 1
            if incoming["id"] == request_id:
                return incoming

        raise TimeoutError(f"timed out waiting for {method}")

    def notify(self, method: str, params: Any = MISSING) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not MISSING:
            message["params"] = params
        self._write_json_line(message)

    def _write_json_line(self, message: dict[str, Any]) -> None:
        if self.proc.stdin is None:
            raise RuntimeError("app-server stdin is unavailable")

        raw = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()
        self.stats.bytes_sent += len(raw)

    def _read_json_line(self) -> dict[str, Any]:
        if self.proc.stdout is None:
            raise RuntimeError("app-server stdout is unavailable")

        raw = self.proc.stdout.readline()
        if not raw:
            detail = ""
            if self.proc.stderr is not None:
                stderr = self.proc.stderr.read() or b""
                detail = stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"app-server exited unexpectedly: {detail}")

        self.stats.bytes_received += len(raw)
        return json.loads(raw.decode("utf-8"))


def find_codex_cli_exe() -> str:
    codex = shutil.which("codex")
    if codex is None:
        raise FileNotFoundError("Cannot find codex on PATH")
    return codex


def find_vscode_codex_exe() -> str:
    extension_dirs = sorted(
        VSCODE_EXTENSIONS_DIR.glob("openai.chatgpt-*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for extension_dir in extension_dirs:
        candidate = extension_dir / "bin" / "windows-x86_64" / "codex.exe"
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("Cannot find VS Code Codex extension codex.exe")


def find_codex_executables(
    sources: list[str] | tuple[str, ...] | str | None = None,
) -> list[dict[str, str]]:
    requested_sources = _normalize_sources(sources)
    finders = {
        "codexcli": find_codex_cli_exe,
        "vscode": find_vscode_codex_exe,
    }
    executables = []
    for source in SOURCE_PRIORITY:
        if source not in requested_sources:
            continue
        try:
            executables.append({"source": source, "path": finders[source]()})
        except (FileNotFoundError, OSError):
            continue
    return executables


def _normalize_sources(
    sources: list[str] | tuple[str, ...] | str | None = None,
) -> list[str]:
    if sources is None:
        return list(SOURCE_PRIORITY)
    if isinstance(sources, str):
        sources = [sources]
    normalized = [source for source in sources if source in SOURCE_PRIORITY]
    return normalized or list(SOURCE_PRIORITY)


def build_server(
    sources: list[str] | tuple[str, ...] | str | None = None,
) -> tuple[CodexAppServer, list[str]]:
    executables = find_codex_executables(sources)
    if not executables:
        searched = ", ".join(_normalize_sources(sources))
        raise FileNotFoundError(f"Cannot find any Codex executable from: {searched}")
    selected = executables[0]
    return (
        CodexAppServer(source=selected["source"], codex_exe=selected["path"]),
        [executable["source"] for executable in executables],
    )


def read_codexbar_info(
    sources: list[str] | tuple[str, ...] | str | None = None,
) -> dict[str, Any]:
    server, available_sources = build_server(sources)
    try:
        initialize = request_or_error(
            server,
            "initialize",
            {
                "clientInfo": {
                    "name": "cocodexbar-demo",
                    "title": "Cocodexbar Demo",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "mcpServerOpenaiFormElicitation": True,
                    "requestAttestation": False,
                },
            },
        )
        server.notify("initialized")

        account = request_or_error(server, "account/read", {"refreshToken": False})
        rate_limits = request_or_error(server, "account/rateLimits/read", None)
        usage = request_or_error(server, "account/usage/read", None)
        config = request_or_error(
            server,
            "config/read",
            {"cwd": str(ROOT), "includeLayers": False},
        )
        models = request_or_error(
            server,
            "model/list",
            {"limit": 50, "cursor": None, "includeHidden": False},
        )

        return {
            "ok": all(
                item["ok"]
                for item in [initialize, account, rate_limits, usage, config, models]
            ),
            "source": available_sources,
            "appServer": {
                "pid": server.proc.pid,
                **server.stats.snapshot(),
            },
            "server": extract_initialize(initialize),
            "account": extract_account(account),
            "quota": extract_quota(rate_limits),
            "usage": extract_usage(usage),
            "model": extract_model(config, models),
            "rawErrors": {
                key: value["error"]
                for key, value in {
                    "initialize": initialize,
                    "account": account,
                    "rateLimits": rate_limits,
                    "usage": usage,
                    "config": config,
                    "models": models,
                }.items()
                if not value["ok"]
            },
        }
    finally:
        server.close()


def request_or_error(
    server: CodexAppServer,
    method: str,
    params: Any = MISSING,
) -> dict[str, Any]:
    try:
        response = server.request(method, params)
    except Exception as error:  # noqa: BLE001 - demo returns structured errors.
        return {"ok": False, "error": str(error), "result": None}

    if "error" in response:
        return {"ok": False, "error": response["error"], "result": None}
    return {"ok": True, "error": None, "result": response.get("result")}


def extract_initialize(response: dict[str, Any]) -> dict[str, Any] | None:
    result = response.get("result")
    if not response["ok"] or not isinstance(result, dict):
        return None
    return {
        "userAgent": result.get("userAgent"),
        "codexHome": result.get("codexHome"),
        "platformFamily": result.get("platformFamily"),
        "platformOs": result.get("platformOs"),
    }


def extract_account(response: dict[str, Any]) -> dict[str, Any] | None:
    result = response.get("result")
    if not response["ok"] or not isinstance(result, dict):
        return None

    account = result.get("account") or {}
    return {
        "requiresOpenaiAuth": result.get("requiresOpenaiAuth"),
        "type": account.get("type"),
        "email": account.get("email"),
        "planType": account.get("planType"),
    }


def extract_quota(response: dict[str, Any]) -> dict[str, Any] | None:
    result = response.get("result")
    if not response["ok"] or not isinstance(result, dict):
        return None

    primary = pick_rate_limit(result)
    return {
        "limitId": primary.get("limitId"),
        "limitName": primary.get("limitName"),
        "planType": primary.get("planType"),
        "primary": simplify_window(primary.get("primary")),
        "secondary": simplify_window(primary.get("secondary")),
        "credits": primary.get("credits"),
        "individualLimit": primary.get("individualLimit"),
        "rateLimitReachedType": primary.get("rateLimitReachedType"),
        "spendControlReached": primary.get("spendControlReached"),
        "resetCredits": result.get("rateLimitResetCredits"),
        "allBuckets": result.get("rateLimitsByLimitId"),
    }


def pick_rate_limit(result: dict[str, Any]) -> dict[str, Any]:
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        codex_bucket = by_id.get("codex")
        if isinstance(codex_bucket, dict):
            return codex_bucket
    rate_limits = result.get("rateLimits")
    return rate_limits if isinstance(rate_limits, dict) else {}


def simplify_window(window: Any) -> dict[str, Any] | None:
    if not isinstance(window, dict):
        return None

    used_percent = window.get("usedPercent")
    return {
        "usedPercent": used_percent,
        "remainingPercent": (
            100 - used_percent if isinstance(used_percent, int) else None
        ),
        "resetsAt": window.get("resetsAt"),
        "windowDurationMins": window.get("windowDurationMins"),
    }


def extract_usage(response: dict[str, Any]) -> dict[str, Any] | None:
    result = response.get("result")
    if not response["ok"] or not isinstance(result, dict):
        return None

    summary = result.get("summary") or {}
    return {
        "lifetimeTokens": summary.get("lifetimeTokens"),
        "currentStreakDays": summary.get("currentStreakDays"),
        "longestStreakDays": summary.get("longestStreakDays"),
        "peakDailyTokens": summary.get("peakDailyTokens"),
        "longestRunningTurnSec": summary.get("longestRunningTurnSec"),
        "dailyUsageBuckets": result.get("dailyUsageBuckets"),
    }


def extract_model(
    config_response: dict[str, Any],
    models_response: dict[str, Any],
) -> dict[str, Any]:
    config = {}
    if config_response["ok"] and isinstance(config_response.get("result"), dict):
        config = config_response["result"].get("config") or {}

    models = []
    default_model = None
    if models_response["ok"] and isinstance(models_response.get("result"), dict):
        models = models_response["result"].get("data") or []
        default_model = next(
            (model for model in models if model.get("isDefault") is True),
            None,
        )

    current_model = config.get("model")
    selected_model = next(
        (
            model
            for model in models
            if model.get("model") == current_model or model.get("id") == current_model
        ),
        default_model,
    )

    return {
        "current": current_model,
        "reasoningEffort": config.get("model_reasoning_effort"),
        "serviceTier": config.get("service_tier"),
        "default": summarize_model(default_model),
        "selected": summarize_model(selected_model),
        "availableCount": len(models),
    }


def summarize_model(model: Any) -> dict[str, Any] | None:
    if not isinstance(model, dict):
        return None
    return {
        "id": model.get("id"),
        "model": model.get("model"),
        "displayName": model.get("displayName"),
        "description": model.get("description"),
        "defaultReasoningEffort": model.get("defaultReasoningEffort"),
        "supportedReasoningEfforts": model.get("supportedReasoningEfforts"),
        "serviceTiers": model.get("serviceTiers"),
    }

#初步解析函数
def parse_frontend_display_result(result: dict[str, Any]) -> dict[str, Any]:
    account = result.get("account") or {}
    quota = result.get("quota") or {}
    primary_quota = quota.get("primary") or {}
    credits = quota.get("credits") or {}
    reset_credits = quota.get("resetCredits") or {}
    usage = result.get("usage") or {}
    model = result.get("model") or {}
    selected_model = model.get("selected") or {}
    app_server = result.get("appServer") or {}
    raw_errors = result.get("rawErrors") or {}

    resets_at = primary_quota.get("resetsAt")
    now = int(time.time())

    return {
        "ok": result.get("ok") is True,
        "source": result.get("source"),
        "account": {
            "email": account.get("email"),
            "planType": account.get("planType"),
            "type": account.get("type"),
            "requiresOpenaiAuth": account.get("requiresOpenaiAuth"),
        },
        "quota": {
            "limitId": quota.get("limitId"),
            "limitName": quota.get("limitName"),
            "usedPercent": primary_quota.get("usedPercent"),
            "remainingPercent": primary_quota.get("remainingPercent"),
            "resetsAt": resets_at,
            "resetsInSeconds": (
                max(0, resets_at - now) if isinstance(resets_at, int) else None
            ),
            "windowDurationMins": primary_quota.get("windowDurationMins"),
            "hasCredits": credits.get("hasCredits"),
            "creditsUnlimited": credits.get("unlimited"),
            "creditsBalance": credits.get("balance"),
            "resetCreditsAvailable": reset_credits.get("availableCount"),
            "spendControlReached": quota.get("spendControlReached"),
            "rateLimitReachedType": quota.get("rateLimitReachedType"),
        },
        "usage": {
            "lifetimeTokens": usage.get("lifetimeTokens"),
            "peakDailyTokens": usage.get("peakDailyTokens"),
            "currentStreakDays": usage.get("currentStreakDays"),
            "longestStreakDays": usage.get("longestStreakDays"),
            "longestRunningTurnSec": usage.get("longestRunningTurnSec"),
        },
        "model": {
            "current": model.get("current"),
            "displayName": selected_model.get("displayName"),
            "description": selected_model.get("description"),
            "reasoningEffort": model.get("reasoningEffort"),
            "serviceTier": model.get("serviceTier"),
            "defaultModel": (model.get("default") or {}).get("model"),
            "availableCount": model.get("availableCount"),
        },
        "appServer": {
            "pid": app_server.get("pid"),
            "requests": app_server.get("requests"),
            "responses": app_server.get("responses"),
            "bytesSent": app_server.get("bytesSent"),
            "bytesReceived": app_server.get("bytesReceived"),
            "uptimeMs": app_server.get("uptimeMs"),
        },
        "errors": raw_errors,
    }


def main() -> int:
    result = read_codexbar_info()
    #print(json.dumps(result, indent=2, ensure_ascii=False))
    
    show_keyword=parse_frontend_display_result(result)
    print(json.dumps(show_keyword, indent=2, ensure_ascii=False))
    
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
