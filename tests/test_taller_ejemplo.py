"""El YAML de ejemplo de un taller: se valida, se ejecuta de verdad y se pasa por el plan B."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import Runner
from pydantic import TypeAdapter

from praxis_runner import ejecucion_tests
from praxis_runner.checks import Comprobacion

YAML = yaml.safe_load((Path(__file__).parent / "ejemplos" / "taller_ejemplo.yaml").read_text(encoding="utf-8"))
ADAPTER = TypeAdapter(Comprobacion)


def peticion(pegado: bool = False) -> list[dict]:
    pasos = []
    for paso in YAML["pasos"]:
        item = {"id": paso["id"], **paso["comprobacion"]}
        if "pegado" in paso:
            item["pegado"] = paso["pegado"]
        if pegado:
            item["salida"] = paso["pegado_ejemplo"]
        pasos.append(item)
    return pasos


def test_every_step_of_the_example_validates() -> None:
    for item in peticion():
        ADAPTER.validate_python(item)


def test_plan_b_accepts_the_sample_pasted_outputs(runner: Runner) -> None:
    r = runner.client.post("/check", json={"comprobaciones": peticion(pegado=True)}, headers=runner.auth())
    data = r.json()
    fallos = [(x["id"], x["mensaje"]) for x in data["resultados"] if not x["ok"]]
    assert r.status_code == 200
    assert fallos == []
    assert data["todo_ok"] is True


@pytest.mark.skipif(shutil.which("git") is None, reason="git no está instalado")
def test_example_workflow_with_the_real_runner(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ejecucion_tests, "comando_tests", lambda cwd: [sys.executable, "-m", *ejecucion_tests.PYTEST_FLAGS]
    )
    ws = runner.workspace("demo")
    h = runner.auth()

    def correr() -> dict[str, bool]:
        r = runner.client.post("/check", json={"comprobaciones": peticion()}, headers=h)
        assert r.status_code == 200, r.text
        return {x["id"]: x["ok"] for x in r.json()["resultados"]}

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], cwd=ws, check=True, capture_output=True
        )

    inicial = correr()
    assert inicial["git-instalado"] is True
    assert not any(
        inicial[k]
        for k in ("carpeta-proyecto", "readme", "repo-git", "dos-commits", "remoto", "tests-verdes", "arbol-limpio")
    )

    (ws / "src").mkdir()
    (ws / "README.md").write_text("# Mi proyecto\n", encoding="utf-8")
    # pytest deja __pycache__ (y .pytest_cache): sin .gitignore el árbol no queda limpio. En el PC de desarrollo lo
    # tapaba PYTHONDONTWRITEBYTECODE=1; en la CI no está y el test fallaba ya en la rama `runner`.
    (ws / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (ws / "test_demo.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "uno")
    (ws / "notas.txt").write_text("x", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "dos")
    git("remote", "add", "origin", "https://github.com/ana/demo.git")
    final = correr()
    assert final == dict.fromkeys(final, True) or [k for k, v in final.items() if not v] == []
