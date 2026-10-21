"""Lista blanca de comprobaciones para POST /check.

El navegador manda comprobaciones tipadas (las del YAML del taller), nunca comandos: cada `tipo` tiene
una forma fija, los comandos salen de la tabla COMANDOS por su identificador y cualquier campo o
tipo desconocido se rechaza (422). Si una comprobación trae `salida`, es el plan B: el alumno pegó
la salida del comando y se interpreta con las mismas reglas, sin ejecutar nada.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .ejecucion_tests import ejecutar_tests, plural
from .herramientas import FIRMAS, detectar
from .proc import run_limited
from .salida import Espera, ShortText, evaluar_salida
from .workspace import RutaNoPermitida, resolver

# Identificador -> (argumentos reales, texto que ve el alumno). Los de git llevan -c core.fsmonitor=false
# para que una configuración maliciosa de un repo clonado no ejecute nada al mirar su estado.
_GIT = ["git", "-c", "core.fsmonitor=false"]
COMANDOS: dict[str, tuple[list[str], str]] = {
    "uv_version": (["uv", "--version"], "uv --version"),
    "git_version": (["git", "--version"], "git --version"),
    "python_version": (["python", "--version"], "python --version"),
    "uv_run_python": (["uv", "run", "python", "--version"], "uv run python --version"),
    "ruff_check": (["uv", "run", "ruff", "check", "."], "uv run ruff check ."),
    "pytest_collect": (["uv", "run", "pytest", "--collect-only", "-q"], "uv run pytest --collect-only -q"),
    "git_status": ([*_GIT, "status", "--short", "--branch"], "git status --short --branch"),
    "git_log": ([*_GIT, "log", "--oneline", "-n", "20"], "git log --oneline -n 20"),
    "git_branch": ([*_GIT, "branch", "--show-current"], "git branch --show-current"),
    "git_remote": ([*_GIT, "remote", "-v"], "git remote -v"),
    "gh_version": (["gh", "--version"], "gh --version"),
    "code_version": (["code", "--version"], "code --version"),
}
COMANDOS_SIN_CARPETA = frozenset({"uv_version", "git_version", "python_version", "gh_version", "code_version"})
ComandoId = Literal[
    "uv_version",
    "git_version",
    "python_version",
    "uv_run_python",
    "ruff_check",
    "pytest_collect",
    "git_status",
    "git_log",
    "git_branch",
    "git_remote",
    "gh_version",
    "code_version",
]
HerramientaNombre = Literal["python", "uv", "git", "code", "gh"]

CHECK_TIMEOUT = 20.0
TESTS_TIMEOUT = 120.0
TOTAL_BUDGET = 150.0
MAX_FILE_BYTES = 1024 * 1024
MAX_DETAIL_CHARS = 2000

Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
Ruta = Annotated[str, Field(min_length=1, max_length=200)]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Id
    salida: str | None = Field(default=None, max_length=64 * 1024)  # plan B: salida pegada por el alumno


class Pegado(BaseModel):
    """Plan B explícito: qué comando pegar y qué reglas cumple su salida."""

    model_config = ConfigDict(extra="forbid")

    comando: ShortText
    espera: Espera


class _ConPegado(_Base):
    pegado: Pegado | None = None


class FicheroExiste(_ConPegado):
    tipo: Literal["fichero_existe"]
    ruta: Ruta


class CarpetaExiste(_ConPegado):
    tipo: Literal["carpeta_existe"]
    ruta: Ruta


class FicheroContiene(_ConPegado):
    tipo: Literal["fichero_contiene"]
    ruta: Ruta
    contiene: list[ShortText] = Field(min_length=1, max_length=10)


class Comando(_ConPegado):
    tipo: Literal["comando"]
    comando: ComandoId
    espera: Espera = Field(default_factory=Espera)


class Herramienta(_ConPegado):
    tipo: Literal["herramienta"]
    nombre: HerramientaNombre
    version_min: str | None = Field(default=None, pattern=r"^\d+(\.\d+){0,3}$")


class GitRepo(_ConPegado):
    tipo: Literal["git_repo"]


class GitCommits(_ConPegado):
    tipo: Literal["git_commits"]
    min: int = Field(default=1, ge=0, le=100_000)


class GitLimpio(_ConPegado):
    tipo: Literal["git_limpio"]


class GitRemoto(_ConPegado):
    tipo: Literal["git_remoto"]
    contiene: ShortText = "github.com"


class PasanTests(_ConPegado):
    tipo: Literal["tests_pasan"]
    min_pasados: int = Field(default=1, ge=0, le=10_000)


Comprobacion = Annotated[
    FicheroExiste
    | CarpetaExiste
    | FicheroContiene
    | Comando
    | Herramienta
    | GitRepo
    | GitCommits
    | GitLimpio
    | GitRemoto
    | PasanTests,
    Field(discriminator="tipo"),
]
COMPROBACION_ADAPTER: TypeAdapter = TypeAdapter(Comprobacion)


class PeticionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comprobaciones: list[Comprobacion] = Field(min_length=1, max_length=40)


@dataclass
class Contexto:
    """Dónde y hasta cuándo se comprueba."""

    carpeta: Path | None  # carpeta de trabajo ya resuelta dentro de la raíz
    deadline: float  # time.monotonic() límite de toda la petición


# ---------------------------------------------------------------------------------------------
# Plan B: reglas de la salida pegada
# ---------------------------------------------------------------------------------------------


def reglas_pegado(chk: _Base) -> tuple[str, Espera] | None:
    """(comando que se le pide pegar, reglas de su salida) o None si el paso solo va con runner."""
    explicito = getattr(chk, "pegado", None)
    if explicito is not None:
        return explicito.comando, explicito.espera
    if isinstance(chk, Comando):
        return COMANDOS[chk.comando][1], chk.espera.model_copy(update={"codigo_salida": None})
    if isinstance(chk, Herramienta):
        return f"{chk.nombre} --version", Espera(regex=FIRMAS[chk.nombre], version_min=chk.version_min)
    if isinstance(chk, GitRepo):
        return "git rev-parse --is-inside-work-tree", Espera(regex=r"^true$")
    if isinstance(chk, GitCommits):
        return "git rev-list --count HEAD", Espera(regex=r"^[0-9]+$", numero_min=chk.min)
    if isinstance(chk, GitRemoto):
        return "git remote -v", Espera(contiene=[chk.contiene])
    if isinstance(chk, PasanTests):
        return "uv run pytest -q", Espera(pytest_min_pasados=chk.min_pasados)
    return None


def evaluar_pegado(chk: _Base, texto: str) -> dict:
    reglas = reglas_pegado(chk)
    if reglas is None:
        return _resultado(chk, False, "Este paso solo se puede comprobar con el runner.", "pegada")
    ok, mensaje = evaluar_salida(texto, reglas[1])
    return _resultado(chk, ok, mensaje, "pegada")


# ---------------------------------------------------------------------------------------------
# Ejecución real
# ---------------------------------------------------------------------------------------------


def _resultado(chk: _Base, ok: bool, mensaje: str, evidencia: str, detalle: str | None = None) -> dict:
    out = {"id": chk.id, "tipo": getattr(chk, "tipo", ""), "ok": ok, "mensaje": mensaje, "evidencia": evidencia}
    if detalle:
        out["detalle"] = detalle[-MAX_DETAIL_CHARS:]
    return out


def _tiempo(ctx: Contexto, maximo: float) -> float:
    return min(maximo, ctx.deadline - time.monotonic())


def _correr(argv: list[str], ctx: Contexto, maximo: float = CHECK_TIMEOUT):
    cwd = ctx.carpeta or Path.home()
    return run_limited(argv, cwd, max(0.5, _tiempo(ctx, maximo)))


def _git_en_carpeta(chk: _Base, ctx: Contexto, argv: list[str]):
    """Ejecuta git solo si la carpeta de trabajo es la raíz de un repo (no un repo de un padre)."""
    assert ctx.carpeta is not None
    if not (ctx.carpeta / ".git").exists():
        return None, _resultado(chk, False, "Esta carpeta todavía no es un repositorio git (falta git init).", "runner")
    return _correr([*_GIT, *argv], ctx), None


def _fallo_proceso(chk: _Base, r, nombre: str) -> dict | None:
    if r.not_found:
        return _resultado(chk, False, f"No encuentro «{nombre}» en tu PATH. ¿Está instalado?", "runner")
    if r.expired:
        return _resultado(chk, False, "El comando tardó demasiado y se paró.", "runner", r.output)
    return None


def _ejecutar_uno(chk: _Base, ctx: Contexto) -> dict:
    """Comprobación local de un paso. Las rutas ya se validan contra la carpeta de trabajo."""
    necesita_carpeta = not isinstance(chk, Herramienta) and not (
        isinstance(chk, Comando) and chk.comando in COMANDOS_SIN_CARPETA
    )
    if necesita_carpeta and ctx.carpeta is None:
        return _resultado(chk, False, "Elige primero tu carpeta de trabajo.", "runner")
    if time.monotonic() >= ctx.deadline:
        return _resultado(chk, False, "Se agotó el tiempo total de la comprobación.", "runner")

    if isinstance(chk, FicheroExiste | CarpetaExiste | FicheroContiene):
        assert ctx.carpeta is not None
        try:
            ruta = resolver(ctx.carpeta, chk.ruta, permitir_ocultos=True)
        except RutaNoPermitida as e:
            return _resultado(chk, False, f"Ruta no permitida: {e}", "runner")
        if isinstance(chk, CarpetaExiste):
            ok = ruta.is_dir()
            return _resultado(
                chk, ok, f"Existe la carpeta {chk.ruta}." if ok else f"No encuentro la carpeta {chk.ruta}.", "runner"
            )
        if not ruta.is_file():
            return _resultado(chk, False, f"No encuentro el fichero {chk.ruta}.", "runner")
        if isinstance(chk, FicheroExiste):
            return _resultado(chk, True, f"Existe {chk.ruta}.", "runner")
        if ruta.stat().st_size > MAX_FILE_BYTES:
            return _resultado(chk, False, f"{chk.ruta} es demasiado grande para comprobarlo.", "runner")
        texto = ruta.read_text(encoding="utf-8", errors="replace")
        for trozo in chk.contiene:
            if trozo not in texto:
                return _resultado(chk, False, f"En {chk.ruta} no veo «{trozo}».", "runner")
        return _resultado(chk, True, f"{chk.ruta} contiene lo esperado.", "runner")

    if isinstance(chk, Comando):
        r = _correr(COMANDOS[chk.comando][0], ctx)
        if (fallo := _fallo_proceso(chk, r, COMANDOS[chk.comando][0][0])) is not None:
            return fallo
        ok, mensaje = evaluar_salida(r.output, chk.espera, r.code)
        return _resultado(chk, ok, mensaje, "runner", None if ok else r.output)

    if isinstance(chk, Herramienta):
        info = detectar(chk.nombre, ctx.carpeta)
        if not info["instalado"]:
            return _resultado(chk, False, f"No encuentro {chk.nombre} en tu PATH. ¿Está instalado?", "runner")
        if chk.version_min:
            ok, mensaje = evaluar_salida(info["version"] or "", Espera(version_min=chk.version_min))
            if not ok:
                return _resultado(chk, False, f"{chk.nombre}: {mensaje}", "runner")
        return _resultado(chk, True, f"{chk.nombre} {info['version']} instalado.", "runner")

    if isinstance(chk, GitRepo):
        r, error = _git_en_carpeta(chk, ctx, ["rev-parse", "--show-toplevel"])
        if error:
            return error
        if (fallo := _fallo_proceso(chk, r, "git")) is not None:
            return fallo
        assert ctx.carpeta is not None
        ok = r.code == 0 and Path(r.output.strip()).resolve() == ctx.carpeta.resolve()
        return _resultado(
            chk, ok, "Repositorio git listo." if ok else "git no reconoce esta carpeta como repositorio.", "runner"
        )

    if isinstance(chk, GitCommits):
        r, error = _git_en_carpeta(chk, ctx, ["rev-list", "--count", "HEAD"])
        if error:
            return error
        if (fallo := _fallo_proceso(chk, r, "git")) is not None:
            return fallo
        if r.code != 0:
            return _resultado(chk, False, "Todavía no hay ningún commit (haz git add y git commit).", "runner")
        n = int(r.output.strip() or 0)
        ok = n >= chk.min
        return _resultado(
            chk,
            ok,
            f"Hay {plural(n, 'commit', 'commits')}."
            if ok
            else f"Hay {plural(n, 'commit', 'commits')} y se piden al menos {chk.min}.",
            "runner",
        )

    if isinstance(chk, GitLimpio):
        r, error = _git_en_carpeta(chk, ctx, ["status", "--porcelain"])
        if error:
            return error
        if (fallo := _fallo_proceso(chk, r, "git")) is not None:
            return fallo
        ok = r.code == 0 and not r.output.strip()
        return _resultado(
            chk, ok, "Todo está commiteado." if ok else "Hay cambios sin commitear.", "runner", None if ok else r.output
        )

    if isinstance(chk, GitRemoto):
        r, error = _git_en_carpeta(chk, ctx, ["remote", "-v"])
        if error:
            return error
        if (fallo := _fallo_proceso(chk, r, "git")) is not None:
            return fallo
        ok = r.code == 0 and chk.contiene in r.output
        return _resultado(
            chk,
            ok,
            "El repo tiene el remoto esperado."
            if ok
            else f"No veo un remoto con «{chk.contiene}» (git remote add origin …).",
            "runner",
        )

    if isinstance(chk, PasanTests):
        assert ctx.carpeta is not None
        t = ejecutar_tests(ctx.carpeta, _tiempo(ctx, TESTS_TIMEOUT))
        ok = t["ok"] and t["fallados"] == 0 and t["errores"] == 0 and t["pasados"] >= chk.min_pasados
        mensaje = (
            t["mensaje"]
            if not t["ok"] or ok
            else f"{plural(t['pasados'], 'test pasa', 'tests pasan')} y se piden al menos {chk.min_pasados}."
        )
        return _resultado(chk, ok, mensaje, "runner", None if ok else t["salida"])

    return _resultado(
        chk, False, "Tipo de comprobación no soportado.", "runner"
    )  # inalcanzable: lo frena la validación


def ejecutar_comprobaciones(comprobaciones: list[_Base], carpeta: Path | None) -> list[dict]:
    """Evalúa en orden cada paso: pegado si trae `salida`, real si no."""
    ctx = Contexto(carpeta=carpeta, deadline=time.monotonic() + TOTAL_BUDGET)
    resultados = []
    for chk in comprobaciones:
        if chk.salida is not None:
            resultados.append(evaluar_pegado(chk, chk.salida))
        else:
            resultados.append(_ejecutar_uno(chk, ctx))
    return resultados
