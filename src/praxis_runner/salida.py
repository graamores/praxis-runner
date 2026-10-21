"""Reglas para interpretar la salida de un comando: las mismas para la salida real y para la pegada.

Hay dos implementaciones de estas reglas, una aquí y otra en `app/js/verificar_salida.js` (el plan B
sin runner corre en el navegador). Se mantienen iguales con los vectores compartidos de
`app/pruebas/vectores_salida.json`, que prueban los dos lados. Si cambias una regla, cambia las dos
y añade un vector.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TEXT_FOR_REGEX = 20_000
MAX_REGEX_CHARS = 120

# Una regex que viene del navegador no puede colgar al runner: sin reglas con retroceso explosivo.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")
_BACKREFERENCE = re.compile(r"\\[1-9]")
_UNSAFE_GROUP = re.compile(r"\(\?(?!:)")  # solo se admite (?: ...); nada de lookarounds ni banderas en línea


def regex_segura(patron: str) -> bool:
    """True si el patrón es corto, portable entre Python y JavaScript y sin retroceso explosivo."""
    if not patron or len(patron) > MAX_REGEX_CHARS:
        return False
    if _BACKREFERENCE.search(patron) or _UNSAFE_GROUP.search(patron) or _NESTED_QUANTIFIER.search(patron):
        return False
    try:
        re.compile(patron)
    except re.error:
        return False
    return True


ShortText = Annotated[str, Field(min_length=1, max_length=200)]


class Espera(BaseModel):
    """Qué tiene que cumplir la salida de un comando. Todas las reglas dadas se aplican (AND)."""

    model_config = ConfigDict(extra="forbid")

    contiene: list[ShortText] = Field(default_factory=list, max_length=10)
    no_contiene: list[ShortText] = Field(default_factory=list, max_length=10)
    regex: str | None = None  # búsqueda por líneas (multilínea); ver regex_segura
    lineas_min: int | None = Field(default=None, ge=0, le=1000)  # líneas no vacías
    version_min: str | None = Field(default=None, pattern=r"^\d+(\.\d+){0,3}$")  # primera versión de la salida
    numero_min: int | None = Field(default=None, ge=0, le=10**9)  # primer entero de la salida
    pytest_min_pasados: int | None = Field(default=None, ge=0, le=10_000)  # resumen de pytest, sin fallos
    codigo_salida: int | None = Field(default=None, ge=0, le=255)  # solo con salida real, no pegada

    @field_validator("regex")
    @classmethod
    def _check_regex(cls, value: str | None) -> str | None:
        if value is not None and not regex_segura(value):
            raise ValueError("regex no permitida: demasiado larga, con retroceso o con grupos especiales")
        return value


_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+|[0-9]+")
_INT_RE = re.compile(r"[0-9]+")
_PYTEST_COUNTS = re.compile(r"([0-9]+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)")
_PYTEST_LINE = re.compile(r" in [0-9]+(?:\.[0-9]+)?s\b")


def _version_tuple(texto: str) -> tuple[int, ...]:
    return tuple(int(p) for p in texto.split("."))


def _cmp_version(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def resumen_pytest(texto: str) -> dict[str, int] | None:
    """Cuenta de la última línea de resumen de pytest ('3 passed, 1 failed in 0.2s') o None."""
    for linea in reversed(texto.split("\n")):
        if _PYTEST_LINE.search(linea) and re.search(r"passed|failed|error", linea):
            conteo: dict[str, int] = {}
            for numero, clave in _PYTEST_COUNTS.findall(linea):
                clave = "errors" if clave == "error" else clave
                conteo[clave] = conteo.get(clave, 0) + int(numero)
            return conteo
    return None


def normalizar(texto: str) -> str:
    """Saltos de línea \\n y sin secuencias de color ANSI, igual que el cliente JavaScript."""
    texto = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", texto)
    return texto.replace("\r\n", "\n").replace("\r", "\n")


def evaluar_salida(texto: str, espera: Espera, codigo: int | None = None) -> tuple[bool, str]:
    """Aplica las reglas a la salida. Devuelve (ok, mensaje en castellano)."""
    texto = normalizar(texto)
    if espera.codigo_salida is not None and codigo is not None and codigo != espera.codigo_salida:
        return False, f"El comando terminó con código {codigo} y se esperaba {espera.codigo_salida}."
    for trozo in espera.contiene:
        if trozo not in texto:
            return False, f"No veo «{trozo}» en la salida."
    for trozo in espera.no_contiene:
        if trozo in texto:
            return False, f"No debería aparecer «{trozo}» en la salida."
    if espera.regex is not None and not re.search(espera.regex, texto[:MAX_TEXT_FOR_REGEX], re.MULTILINE | re.ASCII):
        return False, "La salida no tiene el formato esperado."
    if espera.lineas_min is not None:
        lineas = sum(1 for linea in texto.split("\n") if linea.strip())
        if lineas < espera.lineas_min:
            return False, f"La salida tiene {lineas} líneas y se esperaban al menos {espera.lineas_min}."
    if espera.version_min is not None:
        encontrada = _VERSION_RE.search(texto)
        if not encontrada:
            return False, "No encuentro ningún número de versión en la salida."
        if _cmp_version(_version_tuple(encontrada.group()), _version_tuple(espera.version_min)) < 0:
            return False, f"La versión es {encontrada.group()} y hace falta la {espera.version_min} o posterior."
    if espera.numero_min is not None:
        encontrado = _INT_RE.search(texto)
        if not encontrado:
            return False, "No encuentro ningún número en la salida."
        if int(encontrado.group()) < espera.numero_min:
            return False, f"Sale {encontrado.group()} y se esperaba al menos {espera.numero_min}."
    if espera.pytest_min_pasados is not None:
        conteo = resumen_pytest(texto)
        if conteo is None:
            return False, "No veo la línea final de pytest (algo como «3 passed in 0.02s»)."
        if conteo.get("failed") or conteo.get("errors"):
            return False, "Hay tests que fallan o dan error."
        if conteo.get("passed", 0) < espera.pytest_min_pasados:
            return False, f"Pasan {conteo.get('passed', 0)} tests y se esperaban al menos {espera.pytest_min_pasados}."
    return True, "Correcto."
