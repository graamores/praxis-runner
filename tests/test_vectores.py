"""Las reglas de salida: vectores compartidos con el cliente JavaScript (plan B sin runner)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from praxis_runner.checks import Comprobacion, evaluar_pegado
from praxis_runner.salida import Espera, evaluar_salida, regex_segura, resumen_pytest

# Copia propia de app/pruebas/vectores_salida.json: el runner se publica solo (graamores/praxis-runner) y sus
# tests no pueden depender del resto del repositorio. En el monorepo, build/tests/test_runner_autonomo.py
# exige que las dos copias sean idénticas.
VECTORES_JSON = Path(__file__).resolve().parent / "vectores_salida.json"
VECTORES = json.loads(VECTORES_JSON.read_text(encoding="utf-8"))
RAIZ_APP = Path(__file__).resolve().parents[2] / "app"  # solo existe dentro del monorepo
VERIFICAR_JS = RAIZ_APP / "js" / "verificar_salida.js"
ADAPTER = TypeAdapter(Comprobacion)


@pytest.mark.parametrize("caso", VECTORES["salida"], ids=lambda c: c["nombre"])
def test_salida_vectors(caso: dict) -> None:
    ok, mensaje = evaluar_salida(caso["texto"], Espera(**caso["espera"]), caso.get("codigo"))
    assert ok is caso["ok"], mensaje


@pytest.mark.parametrize("caso", VECTORES["pegado"], ids=lambda c: c["nombre"])
def test_pegado_vectors(caso: dict) -> None:
    chk = ADAPTER.validate_python(caso["comprobacion"])
    res = evaluar_pegado(chk, caso["texto"])
    assert res["ok"] is caso["ok"], res["mensaje"]
    assert res["evidencia"] == "pegada"


@pytest.mark.parametrize("caso", VECTORES["regex_segura"], ids=lambda c: repr(c["patron"]))
def test_regex_segura_vectors(caso: dict) -> None:
    assert regex_segura(caso["patron"]) is caso["segura"]


def test_regex_length_limit() -> None:
    assert regex_segura("a" * 120)
    assert not regex_segura("a" * 121)


def test_evil_regex_cannot_hang_the_runner() -> None:
    """Un patrón con retroceso explosivo ni siquiera se acepta, así que no llega a ejecutarse."""
    with pytest.raises(ValueError):
        Espera(regex="(a+)+$")


def test_resumen_pytest() -> None:
    assert resumen_pytest("1 failed, 2 passed, 1 skipped in 0.5s") == {"failed": 1, "passed": 2, "skipped": 1}
    assert resumen_pytest("2 passed, 3 errors in 1.0s") == {"passed": 2, "errors": 3}
    assert resumen_pytest("nada") is None


# ---------- el mismo juego de vectores contra verificar_salida.js (si hay Node) ----------

NODE = shutil.which("node")
REQUIERE_NODE = bool(os.environ.get("PRAXIS_REQUIRE_NODE"))  # en CI no se puede saltar


@pytest.mark.skipif(
    (NODE is None or not VERIFICAR_JS.exists()) and not REQUIERE_NODE,
    reason="Sin Node o fuera del monorepo (el cliente JS vive en app/); se prueba también en 09_runner_api.html",
)
def test_javascript_implementation_passes_the_same_vectors() -> None:
    assert NODE is not None, "PRAXIS_REQUIRE_NODE está activo y no hay Node en el PATH"
    assert VERIFICAR_JS.exists(), "PRAXIS_REQUIRE_NODE está activo y no está app/js/verificar_salida.js"
    modulo = json.dumps(VERIFICAR_JS.as_uri())
    datos = json.dumps(str(VECTORES_JSON))
    guion = f"""
    import {{ readFileSync }} from "node:fs";
    import {{ evaluarSalida, evaluarPegado, regexSegura }} from {modulo};
    const v = JSON.parse(readFileSync({datos}, "utf8"));
    const fallos = [];
    for (const c of v.salida) {{
      if (evaluarSalida(c.texto, c.espera, c.codigo ?? null).ok !== c.ok) fallos.push("salida: " + c.nombre);
    }}
    for (const c of v.pegado) {{
      if (evaluarPegado(c.comprobacion, c.texto).ok !== c.ok) fallos.push("pegado: " + c.nombre);
    }}
    for (const c of v.regex_segura) {{
      if (regexSegura(c.patron) !== c.segura) fallos.push("regex: " + c.patron);
    }}
    console.log(JSON.stringify(fallos));
    """
    r = subprocess.run(
        [NODE, "--input-type=module", "-e", guion], capture_output=True, text=True, check=False, timeout=60
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1]) == []
