"""Ejecución de procesos con tiempo máximo, salida acotada y árbol de procesos matado entero.

Nunca se usa shell: `run_limited` recibe una lista de argumentos que construye el propio runner
(lista blanca), no texto del navegador.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT_BYTES = 64 * 1024
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_DROPPED_ENV = frozenset({"VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__"})


@dataclass(frozen=True)
class RunResult:
    output: str
    code: int | None  # None si se mató por tiempo o no arrancó
    expired: bool = False
    truncated: bool = False
    not_found: bool = False
    seconds: float = 0.0


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Entorno del proceso hijo: el del alumno, sin el venv del propio runner y sin colores."""
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    env.update(
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        NO_COLOR="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
        UV_NO_PROGRESS="1",
    )
    env.update(extra or {})
    return env


def normalize_text(raw: bytes | str) -> str:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def kill_tree(proc: subprocess.Popen) -> None:
    """Mata el proceso y sus descendientes (en Windows `kill()` deja vivos a los nietos)."""
    if os.name == "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def run_limited(
    argv: list[str],
    cwd: Path,
    timeout: float,
    max_bytes: int = MAX_OUTPUT_BYTES,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Ejecuta `argv` sin shell en `cwd`, con tiempo y salida máximos (stdout y stderr juntos)."""
    env = env if env is not None else clean_env()
    exe = shutil.which(argv[0], path=env.get("PATH"))
    if exe is None:
        return RunResult("", None, not_found=True)
    kwargs: dict = {
        "cwd": cwd,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    start = time.monotonic()
    try:
        proc = subprocess.Popen([exe, *argv[1:]], **kwargs)
    except OSError:
        return RunResult("", None, not_found=True)

    buffer = bytearray()
    flags = {"truncated": False}

    def reader() -> None:
        assert proc.stdout is not None
        while chunk := proc.stdout.read(4096):  # se sigue leyendo aunque se descarte, para no bloquear al hijo
            room = max_bytes - len(buffer)
            if room > 0:
                buffer.extend(chunk[:room])
            if len(chunk) > room:
                flags["truncated"] = True

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    expired = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        expired = True
        kill_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
    thread.join(timeout=2)
    code = None if expired else proc.returncode
    return RunResult(normalize_text(bytes(buffer)), code, expired, flags["truncated"], False, time.monotonic() - start)
