"""Detección de herramientas del alumno (Python, uv, git, VS Code, gh) para GET /hello y las comprobaciones."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .proc import run_limited

# Comando de versión y firma de la primera línea de salida. Si no coincide, no es esa herramienta
# (por ejemplo, el «python.exe» de la Tienda de Microsoft imprime un aviso y no una versión).
HERRAMIENTAS: dict[str, tuple[list[list[str]], str]] = {
    "python": ([["python", "--version"], ["py", "-3", "--version"]], r"^Python [0-9]"),
    "uv": ([["uv", "--version"]], r"^uv [0-9]"),
    "git": ([["git", "--version"]], r"^git version [0-9]"),
    "code": ([["code", "--version"]], r"^[0-9]+\.[0-9]+\.[0-9]+"),
    "gh": ([["gh", "--version"]], r"^gh version [0-9]"),
}
FIRMAS = {nombre: firma for nombre, (_, firma) in HERRAMIENTAS.items()}
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+")
_CACHE_SECONDS = 20.0
_cache: dict = {"at": 0.0, "value": None}
_cache_lock = threading.Lock()


def detectar(nombre: str, cwd: Path | None = None, timeout: float = 8.0) -> dict:
    """Devuelve {"instalado": bool, "version": str | None} para una herramienta de la lista."""
    candidatos, firma = HERRAMIENTAS[nombre]
    cwd = cwd or Path.home()
    for argv in candidatos:
        resultado = run_limited(argv, cwd, timeout, max_bytes=2048)
        if resultado.code == 0 and re.search(firma, resultado.output, re.MULTILINE):
            version = _VERSION_RE.search(resultado.output)
            return {"instalado": True, "version": version.group() if version else None}
    return {"instalado": False, "version": None}


def detectar_todas(cwd: Path | None = None) -> dict[str, dict]:
    """Detecta todas las herramientas en paralelo y guarda el resultado 20 s (VS Code tarda en arrancar)."""
    with _cache_lock:
        if _cache["value"] is not None and time.monotonic() - _cache["at"] < _CACHE_SECONDS:
            return _cache["value"]
    with ThreadPoolExecutor(max_workers=len(HERRAMIENTAS)) as pool:
        futuros = {nombre: pool.submit(detectar, nombre, cwd) for nombre in HERRAMIENTAS}
        valor = {nombre: futuro.result() for nombre, futuro in futuros.items()}
    with _cache_lock:
        _cache.update(at=time.monotonic(), value=valor)
    return valor


def limpiar_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, value=None)
