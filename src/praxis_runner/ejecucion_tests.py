"""POST /test: ejecuta pytest en la carpeta de trabajo con tiempo máximo y salida acotada.

Es lo único que el runner ejecuta de verdad del alumno: sus propios tests, en su propia carpeta,
igual que si escribiera `uv run pytest` él. El navegador no manda código ni argumentos.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .proc import clean_env, run_limited
from .salida import resumen_pytest

DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 300.0
PYTEST_FLAGS = ["pytest", "-q", "--no-header", "--color=no", "-p", "no:cacheprovider"]


def plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def comando_tests(cwd: Path) -> list[str] | None:
    """`uv run pytest` si hay pyproject.toml y uv; si no, `python -m pytest`; None si no hay con qué."""
    env_path = clean_env().get("PATH")
    if (cwd / "pyproject.toml").is_file() and shutil.which("uv", path=env_path):
        return ["uv", "run", *PYTEST_FLAGS]
    if shutil.which("python", path=env_path):
        return ["python", "-m", *PYTEST_FLAGS]
    return None


def ejecutar_tests(cwd: Path, timeout: float = DEFAULT_TIMEOUT) -> dict:
    timeout = max(1.0, min(float(timeout), MAX_TIMEOUT))
    argv = comando_tests(cwd)
    base = {
        "ok": False,
        "comando": " ".join(argv) if argv else None,
        "codigo_salida": None,
        "pasados": 0,
        "fallados": 0,
        "errores": 0,
        "omitidos": 0,
        "expiro": False,
        "truncada": False,
        "duracion_s": 0.0,
        "salida": "",
        "mensaje": "",
    }
    if argv is None:
        base["mensaje"] = "No encuentro uv ni python en tu PATH. Instálalos y vuelve a abrir la terminal del runner."
        return base
    r = run_limited(argv, cwd, timeout)
    conteo = resumen_pytest(r.output) or {}
    base.update(
        codigo_salida=r.code,
        pasados=conteo.get("passed", 0),
        fallados=conteo.get("failed", 0),
        errores=conteo.get("errors", 0),
        omitidos=conteo.get("skipped", 0),
        expiro=r.expired,
        truncada=r.truncated,
        duracion_s=round(r.seconds, 2),
        salida=r.output,
    )
    if r.not_found:
        base["mensaje"] = "No pude arrancar el comando de tests."
    elif r.expired:
        base["mensaje"] = f"Tiempo agotado ({int(timeout)} s): el runner paró los tests. ¿Hay un bucle infinito?"
    elif r.code == 0:
        base["ok"] = base["pasados"] >= 1
        base["mensaje"] = (
            (f"{plural(base['pasados'], 'test pasa', 'tests pasan')}.")
            if base["ok"]
            else "pytest no encontró ningún test que ejecutar."
        )
    elif "No module named pytest" in r.output or "Failed to spawn: `pytest`" in r.output:
        base["mensaje"] = "pytest no está en el proyecto. Añádelo con: uv add --dev pytest"
    elif r.code == 5:
        base["mensaje"] = "pytest no encontró ningún test. Los ficheros deben llamarse test_*.py."
    elif base["fallados"] or base["errores"]:
        malos = base["fallados"] + base["errores"]
        base["mensaje"] = f"{plural(malos, 'test falla', 'tests fallan')}; {plural(base['pasados'], 'pasa', 'pasan')}."
    else:
        base["mensaje"] = "pytest terminó con errores antes de acabar. Mira la salida."
    return base
