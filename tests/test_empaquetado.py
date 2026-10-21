"""Empaquetado (H-03 y H-04, docs/SEGURIDAD.md §8 del repositorio de Praxis).

- Las dependencias de ejecución de pyproject.toml son EXACTAS e iguales a las del lock: `uv tool install` no lee
  uv.lock, así que solo así instala lo mismo que han probado los tests.
- El lock respeta la espera de 14 días (`exclude-newer`) en todo lo que se ejecuta.
- El paquete es autónomo: nada fuera de esta carpeta (se publica tal cual en graamores/praxis-runner).
"""

from __future__ import annotations

import re
import tomllib
from datetime import datetime
from pathlib import Path

import praxis_runner

RAIZ = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
LOCK = tomllib.loads((RAIZ / "uv.lock").read_text(encoding="utf-8"))
PAQUETES = {p["name"]: p for p in LOCK["package"]}


def _norm(nombre: str) -> str:
    return re.sub(r"[-_.]+", "-", nombre).lower()


def _cierre_de_ejecucion() -> dict[str, str]:
    """Paquetes del lock que necesita praxis-runner para ejecutarse (sin el grupo dev)."""
    vistos: dict[str, str] = {}
    pendientes = [d["name"] for d in PAQUETES["praxis-runner"].get("dependencies", [])]
    while pendientes:
        nombre = pendientes.pop()
        if nombre in vistos:
            continue
        paquete = PAQUETES[nombre]
        vistos[nombre] = paquete["version"]
        pendientes += [d["name"] for d in paquete.get("dependencies", [])]
    return vistos


def test_dependencias_exactas_e_iguales_al_lock() -> None:
    declaradas = {}
    for dep in PYPROJECT["project"]["dependencies"]:
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][0-9A-Za-z.+-]*)", dep)
        assert m, f"dependencia sin versión exacta: {dep!r}"
        declaradas[_norm(m.group(1))] = m.group(2)
    assert declaradas == {_norm(k): v for k, v in _cierre_de_ejecucion().items()}


def test_lock_respeta_la_espera_de_14_dias() -> None:
    limite = datetime.fromisoformat(PYPROJECT["tool"]["uv"]["exclude-newer"])
    for nombre in _cierre_de_ejecucion():
        fechas = [w["upload-time"] for w in PAQUETES[nombre].get("wheels", []) if "upload-time" in w]
        sdist = PAQUETES[nombre].get("sdist", {})
        fechas += [sdist["upload-time"]] if "upload-time" in sdist else []
        assert fechas, nombre
        assert min(datetime.fromisoformat(f) for f in fechas) <= limite, f"{nombre}: publicado después de {limite}"


def test_version_coherente() -> None:
    assert PYPROJECT["project"]["version"] == praxis_runner.__version__
    assert PAQUETES["praxis-runner"]["version"] == praxis_runner.__version__


def test_paquete_autonomo() -> None:
    assert (RAIZ / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    assert PYPROJECT["project"]["license"] == "MIT"
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "../" not in readme, "el README no puede enlazar fuera de la carpeta (se publica solo)"
    assert "graamores/praxis-runner@v" in readme, "el README debe instalar desde el repositorio público con etiqueta"
    for fichero in [*RAIZ.glob("src/**/*.py"), *RAIZ.glob("tests/**/*.py")]:
        texto = fichero.read_text(encoding="utf-8")
        # test_vectores.py mira app/ solo si existe (dentro del monorepo); este fichero nombra la cadena.
        if fichero.name not in {"test_vectores.py", "test_empaquetado.py"}:
            assert "parents[2]" not in texto, f"{fichero.name} sale de la carpeta"
