"""Carpeta raíz y carpeta de trabajo: todo lo que el runner toca vive dentro de la raíz.

La raíz la elige el alumno una vez, desde la consola (nunca desde el navegador). El navegador solo
puede pedir subcarpetas relativas, y cada ruta se resuelve y se comprueba contra la raíz real
(enlaces simbólicos y uniones de Windows incluidos) antes de usarla.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MAX_DEPTH = 4
MAX_PATH_CHARS = 200
_FORBIDDEN_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)


class RutaNoPermitida(ValueError):  # noqa: N818 (nombre en castellano, como el resto)
    """La ruta pedida sale de la raíz o no es una ruta relativa válida."""


def _dentro(ruta: Path, base: Path) -> bool:
    try:
        ruta.relative_to(base)
    except ValueError:
        return False
    return True


def validar_raiz(candidata: Path, home: Path | None = None) -> Path:
    """Devuelve la raíz resuelta o lanza RutaNoPermitida si es demasiado amplia.

    Se rechaza la raíz de un disco, la carpeta personal y cualquiera de sus padres (un runner con
    acceso a toda tu carpeta personal vería claves SSH, navegadores, etc.) y las carpetas del sistema.
    """
    home = (home or Path.home()).resolve()
    ruta = candidata.expanduser().resolve()
    if ruta == Path(ruta.anchor):
        raise RutaNoPermitida("La raíz no puede ser un disco entero.")
    if ruta == home or ruta in home.parents:
        raise RutaNoPermitida("La raíz no puede ser tu carpeta personal ni una carpeta que la contenga.")
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        sistema = os.environ.get(var)
        if sistema and _dentro(ruta, Path(sistema).resolve()):
            raise RutaNoPermitida("La raíz no puede estar en una carpeta del sistema.")
    if any(p.name in {".ssh", ".gnupg", ".praxis"} for p in (ruta, *ruta.parents)):
        raise RutaNoPermitida("La raíz no puede estar en una carpeta de claves o de estado.")
    return ruta


def partes_relativas(rel: str, *, permitir_ocultos: bool = False) -> tuple[str, ...]:
    """Valida una ruta relativa escrita con / o \\ y devuelve sus componentes."""
    if not isinstance(rel, str) or not rel.strip():
        raise RutaNoPermitida("Falta la ruta.")
    if len(rel) > MAX_PATH_CHARS:
        raise RutaNoPermitida("La ruta es demasiado larga.")
    texto = rel.replace("\\", "/")
    if texto.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", texto):
        raise RutaNoPermitida("La ruta tiene que ser relativa a tu carpeta.")
    partes = tuple(p for p in texto.split("/") if p not in ("", "."))
    if not partes or len(partes) > MAX_DEPTH:
        raise RutaNoPermitida(f"La ruta debe tener entre 1 y {MAX_DEPTH} niveles.")
    for p in partes:
        if p == "..":
            raise RutaNoPermitida("No se admite «..» en las rutas.")
        if _FORBIDDEN_CHARS.search(p) or p != p.rstrip(" ."):
            raise RutaNoPermitida(f"Nombre no válido: {p!r}.")
        if p.split(".")[0].lower() in _RESERVED_NAMES:
            raise RutaNoPermitida(f"Nombre reservado de Windows: {p!r}.")
        if p.startswith(".") and not permitir_ocultos:
            raise RutaNoPermitida("Los nombres de carpeta no pueden empezar por punto.")
    return partes


def resolver(raiz: Path, rel: str, *, permitir_ocultos: bool = False) -> Path:
    """Resuelve `rel` dentro de `raiz` o lanza RutaNoPermitida (también si un enlace escapa)."""
    raiz_real = raiz.resolve()
    destino = raiz_real.joinpath(*partes_relativas(rel, permitir_ocultos=permitir_ocultos)).resolve()
    if not _dentro(destino, raiz_real):
        raise RutaNoPermitida("La ruta sale de tu carpeta raíz.")
    return destino


def relativa(raiz: Path, ruta: Path) -> str:
    return ruta.resolve().relative_to(raiz.resolve()).as_posix()


def listar_carpetas(raiz: Path, limite: int = 60) -> list[str]:
    """Primer nivel de carpetas de la raíz (sin ocultas), para que el alumno elija."""
    try:
        nombres = sorted(p.name for p in raiz.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
    return nombres[:limite]
