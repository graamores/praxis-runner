"""POST /check (lista blanca, plan B) y POST /test (pytest con tiempo máximo)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import Runner

from praxis_runner import ejecucion_tests
from praxis_runner.checks import COMANDOS, ComandoId
from praxis_runner.config import APP_ORIGIN

HAY_GIT = shutil.which("git") is not None


def check(runner: Runner, *comprobaciones: dict) -> dict:
    r = runner.client.post("/check", json={"comprobaciones": list(comprobaciones)}, headers=runner.auth())
    assert r.status_code == 200, r.text
    return r.json()


def un_resultado(runner: Runner, comprobacion: dict) -> dict:
    return check(runner, comprobacion)["resultados"][0]


# ---------- lista blanca ----------


@pytest.mark.parametrize(
    "mala",
    [
        {"id": "a", "tipo": "shell", "comando": "echo hola"},
        {"id": "a", "tipo": "ejecutar", "codigo": "print(1)"},
        {"id": "a", "tipo": "comando", "comando": "rm -rf /"},
        {"id": "a", "tipo": "comando", "comando": "uv --version"},  # el texto no vale: solo identificadores
        {"id": "a", "tipo": "comando", "comando": "uv_version", "argumentos": ["--help"]},
        {"id": "a", "tipo": "comando", "comando": "uv_version; calc"},
        {"id": "a", "tipo": "comando", "comando": "git_status", "cwd": "C:\\Windows"},
        {"id": "a", "tipo": "fichero_existe"},  # falta ruta
        {"id": "a", "tipo": "herramienta", "nombre": "powershell"},
        {"id": "a b", "tipo": "git_repo"},  # id con espacio
        {"id": "a", "tipo": "git_commits", "min": -1},
        {"id": "a", "tipo": "fichero_contiene", "ruta": "x", "contiene": []},
        {"id": "a", "tipo": "comando", "comando": "uv_version", "espera": {"regex": "(a+)+$"}},  # regex con retroceso
        {"id": "a", "tipo": "comando", "comando": "uv_version", "espera": {"regex": "(?=a)"}},
        {"id": "a", "tipo": "comando", "comando": "uv_version", "espera": {"shell": "x"}},
        {"id": "a", "tipo": "git_repo", "salida": "x" * 70_000},
        {"tipo": "git_repo"},  # sin id
        "git status",
    ],
)
def test_check_rejects_everything_outside_the_whitelist(runner: Runner, mala) -> None:
    runner.workspace()
    r = runner.client.post("/check", json={"comprobaciones": [mala]}, headers=runner.auth())
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "peticion_invalida"


def test_check_rejects_empty_and_huge_lists(runner: Runner) -> None:
    h = runner.auth()
    assert runner.client.post("/check", json={"comprobaciones": []}, headers=h).status_code == 422
    assert (
        runner.client.post(
            "/check", json={"comprobaciones": [{"id": f"p{i}", "tipo": "git_repo"} for i in range(41)]}, headers=h
        ).status_code
        == 422
    )
    assert runner.client.post("/check", json={}, headers=h).status_code == 422
    assert (
        runner.client.post(
            "/check", json={"comprobaciones": [{"id": "a", "tipo": "git_repo"}], "otro": 1}, headers=h
        ).status_code
        == 422
    )


def test_command_table_matches_the_accepted_identifiers() -> None:
    from typing import get_args

    assert set(get_args(ComandoId)) == set(COMANDOS)
    for argv, mostrar in COMANDOS.values():
        assert isinstance(argv, list)
        assert all(isinstance(a, str) for a in argv)
        assert mostrar  # texto para el alumno


def test_no_whitelisted_command_uses_a_shell() -> None:
    for argv, _ in COMANDOS.values():
        assert argv[0] in {"uv", "git", "python", "gh", "code"}
        assert not any(c in a for a in argv for c in ";|&<>`$")


# ---------- comprobaciones locales ----------


def test_check_without_workspace_asks_for_one(runner: Runner) -> None:
    res = un_resultado(runner, {"id": "f", "tipo": "fichero_existe", "ruta": "README.md"})
    assert res["ok"] is False
    assert "carpeta de trabajo" in res["mensaje"]


def test_fichero_existe_y_carpeta_existe(runner: Runner) -> None:
    ws = runner.workspace()
    (ws / "README.md").write_text("# hola", encoding="utf-8")
    (ws / "src").mkdir()
    out = check(
        runner,
        {"id": "a", "tipo": "fichero_existe", "ruta": "README.md"},
        {"id": "b", "tipo": "fichero_existe", "ruta": "NOEXISTE.md"},
        {"id": "c", "tipo": "carpeta_existe", "ruta": "src"},
        {"id": "d", "tipo": "carpeta_existe", "ruta": "README.md"},
        {"id": "e", "tipo": "fichero_existe", "ruta": "src"},
    )
    assert [r["ok"] for r in out["resultados"]] == [True, False, True, False, False]
    assert out["todo_ok"] is False
    assert all(r["evidencia"] == "runner" for r in out["resultados"])


@pytest.mark.parametrize(
    "ruta", ["../secreto.txt", "..\\secreto.txt", "C:\\Windows\\win.ini", "/etc/passwd", "a/../../x", "~/x"]
)
def test_check_paths_cannot_leave_the_workspace(runner: Runner, ruta: str) -> None:
    ws = runner.workspace()
    (ws.parent / "secreto.txt").write_text("clave", encoding="utf-8")
    res = un_resultado(runner, {"id": "f", "tipo": "fichero_contiene", "ruta": ruta, "contiene": ["clave"]})
    assert res["ok"] is False
    assert "no permitida" in res["mensaje"].lower()
    assert "clave" not in res["mensaje"]


def test_fichero_contiene(runner: Runner) -> None:
    ws = runner.workspace()
    (ws / "pyproject.toml").write_text('[project]\nname = "demo"\nrequires-python = ">=3.14"\n', encoding="utf-8")
    ok = un_resultado(
        runner, {"id": "a", "tipo": "fichero_contiene", "ruta": "pyproject.toml", "contiene": ["[project]", "3.14"]}
    )
    ko = un_resultado(runner, {"id": "b", "tipo": "fichero_contiene", "ruta": "pyproject.toml", "contiene": ["pytest"]})
    assert ok["ok"] is True
    assert ko["ok"] is False
    assert "pytest" in ko["mensaje"]


def test_fichero_contiene_refuses_huge_files(runner: Runner) -> None:
    ws = runner.workspace()
    (ws / "grande.txt").write_bytes(b"a" * (2 * 1024 * 1024))
    res = un_resultado(runner, {"id": "g", "tipo": "fichero_contiene", "ruta": "grande.txt", "contiene": ["a"]})
    assert res["ok"] is False


def test_dotfiles_are_checkable_inside_the_workspace(runner: Runner) -> None:
    ws = runner.workspace()
    (ws / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    assert (
        un_resultado(runner, {"id": "i", "tipo": "fichero_contiene", "ruta": ".gitignore", "contiene": [".venv"]})["ok"]
        is True
    )


def test_comando_python_version(runner: Runner) -> None:
    runner.workspace()
    res = un_resultado(
        runner, {"id": "p", "tipo": "comando", "comando": "python_version", "espera": {"regex": "^Python 3"}}
    )
    # En CI y en tu máquina hay un `python` 3 en el PATH; si no, el mensaje lo dice con claridad.
    assert res["ok"] is True or "PATH" in res["mensaje"]


def test_herramienta_not_installed_message(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("praxis_runner.checks.detectar", lambda nombre, cwd=None: {"instalado": False, "version": None})
    res = un_resultado(runner, {"id": "h", "tipo": "herramienta", "nombre": "uv"})
    assert res["ok"] is False
    assert "PATH" in res["mensaje"]


def test_herramienta_version_min(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "praxis_runner.checks.detectar", lambda nombre, cwd=None: {"instalado": True, "version": "3.11.4"}
    )
    assert (
        un_resultado(runner, {"id": "h", "tipo": "herramienta", "nombre": "python", "version_min": "3.10"})["ok"]
        is True
    )
    ko = un_resultado(runner, {"id": "h", "tipo": "herramienta", "nombre": "python", "version_min": "3.14"})
    assert ko["ok"] is False
    assert "3.14" in ko["mensaje"]


# ---------- git ----------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True
    )


@pytest.mark.skipif(not HAY_GIT, reason="git no está instalado")
def test_git_checks_step_by_step(runner: Runner) -> None:
    ws = runner.workspace()
    pasos = [
        {"id": "repo", "tipo": "git_repo"},
        {"id": "commits", "tipo": "git_commits", "min": 1},
        {"id": "limpio", "tipo": "git_limpio"},
        {"id": "remoto", "tipo": "git_remoto", "contiene": "github.com"},
    ]
    assert [r["ok"] for r in check(runner, *pasos)["resultados"]] == [False, False, False, False]

    _git(ws, "init", "-q")
    (ws / "a.txt").write_text("a", encoding="utf-8")
    res = check(runner, *pasos)["resultados"]
    assert res[0]["ok"] is True
    assert res[1]["ok"] is False  # repo sin commits
    assert res[2]["ok"] is False  # a.txt sin añadir

    _git(ws, "add", ".")
    _git(ws, "commit", "-q", "-m", "primero")
    res = check(runner, *pasos)["resultados"]
    assert [r["ok"] for r in res] == [True, True, True, False]

    _git(ws, "remote", "add", "origin", "https://github.com/ana/demo.git")
    assert check(runner, *pasos)["todo_ok"] is True
    assert un_resultado(runner, {"id": "c2", "tipo": "git_commits", "min": 2})["ok"] is False

    (ws / "a.txt").write_text("cambiado", encoding="utf-8")
    assert un_resultado(runner, {"id": "l", "tipo": "git_limpio"})["ok"] is False


@pytest.mark.skipif(not HAY_GIT, reason="git no está instalado")
def test_git_repo_requires_the_workspace_itself_to_be_the_repo(runner: Runner) -> None:
    """Una carpeta dentro de otro repositorio no cuenta como repositorio propio."""
    _git(runner.root, "init", "-q")
    ws = runner.workspace("sub")
    assert un_resultado(runner, {"id": "repo", "tipo": "git_repo"})["ok"] is False
    assert (ws / ".git").exists() is False


@pytest.mark.skipif(not HAY_GIT, reason="git no está instalado")
def test_git_status_command_runs_in_the_workspace(runner: Runner) -> None:
    ws = runner.workspace()
    _git(ws, "init", "-q")
    res = un_resultado(
        runner,
        {"id": "s", "tipo": "comando", "comando": "git_status", "espera": {"contiene": ["##"], "codigo_salida": 0}},
    )
    assert res["ok"] is True


# ---------- plan B: salida pegada ----------


def test_pasted_output_is_evaluated_without_running_anything(runner: Runner) -> None:
    # Sin carpeta de trabajo elegida: el plan B no la necesita.
    out = check(
        runner,
        {"id": "uv", "tipo": "herramienta", "nombre": "uv", "salida": "uv 0.13.3 (abc 2026-10-19)"},
        {"id": "git", "tipo": "herramienta", "nombre": "git", "salida": "uv 0.13.3"},
        {"id": "commits", "tipo": "git_commits", "min": 2, "salida": "3"},
        {"id": "tests", "tipo": "tests_pasan", "min_pasados": 2, "salida": "..\n2 passed in 0.03s"},
        {"id": "solo", "tipo": "git_limpio", "salida": ""},
    )
    assert [r["ok"] for r in out["resultados"]] == [True, False, True, True, False]
    assert all(r["evidencia"] == "pegada" for r in out["resultados"])
    assert "solo se puede comprobar con el runner" in out["resultados"][4]["mensaje"]


def test_pasted_output_never_executes_the_command(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    def prohibido(*args, **kwargs):
        raise AssertionError("el plan B no debe ejecutar procesos")

    monkeypatch.setattr("praxis_runner.checks.run_limited", prohibido)
    monkeypatch.setattr("praxis_runner.checks.detectar", prohibido)
    res = un_resultado(
        runner,
        {
            "id": "u",
            "tipo": "comando",
            "comando": "uv_version",
            "espera": {"regex": "^uv [0-9]"},
            "salida": "uv 0.13.3",
        },
    )
    assert res["ok"] is True


def test_explicit_pegado_rules_for_file_steps(runner: Runner) -> None:
    paso = {
        "id": "f",
        "tipo": "fichero_existe",
        "ruta": "README.md",
        "pegado": {"comando": "dir", "espera": {"contiene": ["README.md"]}},
    }
    assert un_resultado(runner, {**paso, "salida": "README.md  pyproject.toml"})["ok"] is True
    assert un_resultado(runner, {**paso, "salida": "pyproject.toml"})["ok"] is False


@pytest.mark.usefixtures("usa_este_python")
def test_only_one_run_at_a_time(runner: Runner) -> None:
    import threading
    import time

    ws = runner.workspace()
    (ws / "test_lento.py").write_text("import time\n\ndef test_lento():\n    time.sleep(4)\n", encoding="utf-8")
    h = runner.auth()
    hilo = threading.Thread(target=lambda: runner.client.post("/test", json={"timeout_s": 30}, headers=h))
    hilo.start()
    time.sleep(1.5)
    try:
        segundo = runner.client.post(
            "/check", json={"comprobaciones": [{"id": "a", "tipo": "carpeta_existe", "ruta": "x"}]}, headers=h
        )
        assert segundo.status_code == 409
        assert segundo.json()["error"]["code"] == "ocupado"
    finally:
        hilo.join()


# ---------- /test ----------


def proyecto_con_tests(ws: Path, cuerpo: str) -> None:
    (ws / "test_demo.py").write_text(textwrap.dedent(cuerpo), encoding="utf-8")


def test_test_requires_workspace(runner: Runner) -> None:
    r = runner.client.post("/test", json={}, headers=runner.auth())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "sin_carpeta"


@pytest.mark.usefixtures("usa_este_python")
def test_test_passes(runner: Runner) -> None:
    proyecto_con_tests(runner.workspace(), "def test_a():\n    assert 1 + 1 == 2\n\ndef test_b():\n    assert True\n")
    r = runner.client.post("/test", json={}, headers=runner.auth())
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["pasados"] == 2
    assert data["fallados"] == 0
    assert data["expiro"] is False
    assert "2 passed" in data["salida"]


@pytest.mark.usefixtures("usa_este_python")
def test_test_failing(runner: Runner) -> None:
    proyecto_con_tests(runner.workspace(), "def test_a():\n    assert 1 == 2\n\ndef test_b():\n    assert True\n")
    data = runner.client.post("/test", json={}, headers=runner.auth()).json()
    assert data["ok"] is False
    assert data["fallados"] == 1
    assert data["pasados"] == 1
    assert "1 test falla" in data["mensaje"]


@pytest.mark.usefixtures("usa_este_python")
def test_test_without_tests(runner: Runner) -> None:
    runner.workspace()
    data = runner.client.post("/test", json={}, headers=runner.auth()).json()
    assert data["ok"] is False
    assert data["codigo_salida"] == 5


@pytest.mark.usefixtures("usa_este_python")
def test_test_time_limit_kills_infinite_loops(runner: Runner) -> None:
    proyecto_con_tests(runner.workspace(), "import time\n\ndef test_cuelga():\n    time.sleep(120)\n")
    r = runner.client.post("/test", json={"timeout_s": 2}, headers=runner.auth())
    data = r.json()
    assert data["expiro"] is True
    assert data["ok"] is False
    assert data["duracion_s"] < 30
    assert "Tiempo agotado" in data["mensaje"]


@pytest.mark.usefixtures("usa_este_python")
def test_test_output_is_capped(runner: Runner) -> None:
    proyecto_con_tests(runner.workspace(), "def test_ruidoso():\n    print('x' * 500_000)\n    assert False\n")
    data = runner.client.post("/test", json={}, headers=runner.auth()).json()
    assert data["truncada"] is True
    assert len(data["salida"].encode()) <= 64 * 1024


@pytest.mark.usefixtures("usa_este_python")
def test_test_runs_inside_the_workspace(runner: Runner) -> None:
    ws = runner.workspace("mi-carpeta")
    proyecto_con_tests(ws, "import os\n\ndef test_cwd():\n    print('CWD=' + os.getcwd())\n    assert False\n")
    data = runner.client.post("/test", json={}, headers=runner.auth()).json()
    assert "CWD=" in data["salida"]
    assert "mi-carpeta" in data["salida"]


def test_test_accepts_no_arguments_from_the_browser(runner: Runner) -> None:
    runner.workspace()
    h = runner.auth()
    for cuerpo in (
        {"args": ["--collect-only"]},
        {"comando": "calc"},
        {"carpeta": "../.."},
        {"timeout_s": 99999},
        {"timeout_s": 0},
    ):
        assert runner.client.post("/test", json=cuerpo, headers=h).status_code == 422, cuerpo


def test_tests_pasan_check_uses_the_same_runner(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = runner.workspace()
    proyecto_con_tests(ws, "def test_a():\n    assert True\n")
    monkeypatch.setattr(
        ejecucion_tests, "comando_tests", lambda cwd: [sys.executable, "-m", *ejecucion_tests.PYTEST_FLAGS]
    )
    assert un_resultado(runner, {"id": "t", "tipo": "tests_pasan", "min_pasados": 1})["ok"] is True
    ko = un_resultado(runner, {"id": "t", "tipo": "tests_pasan", "min_pasados": 5})
    assert ko["ok"] is False
    assert "al menos 5" in ko["mensaje"]


def test_cors_headers_on_check_for_the_app_origin(runner: Runner) -> None:
    runner.workspace()
    r = runner.client.post(
        "/check",
        json={"comprobaciones": [{"id": "a", "tipo": "carpeta_existe", "ruta": "src"}]},
        headers=runner.auth(),
    )
    assert r.headers["access-control-allow-origin"] == APP_ORIGIN
