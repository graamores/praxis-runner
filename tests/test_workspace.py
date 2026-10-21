"""Carpeta raíz y carpeta de trabajo: todo queda dentro de la raíz que eligió el alumno."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import Runner

from praxis_runner.state import State
from praxis_runner.workspace import RutaNoPermitida, resolver, validar_raiz

MALAS = [
    "..",
    "../fuera",
    "a/../../fuera",
    "a/..\\..\\fuera",
    "/etc",
    "\\Windows",
    "C:\\Windows",
    "c:/Users",
    "~/x",
    ".ssh",
    ".git",
    "a/.oculta",
    "con",
    "NUL",
    "aux.txt",
    "bad:name",
    "bad|name",
    "bad?name",
    "trailing.",
    "trailing ",
    "a/b/c/d/e",
    "",
    "   ",
    "x" * 300,
    "tab\tname",
]


@pytest.mark.parametrize("mala", MALAS)
def test_resolver_rejects_bad_paths(tmp_path: Path, mala: str) -> None:
    with pytest.raises(RutaNoPermitida):
        resolver(tmp_path, mala)


@pytest.mark.parametrize("buena", ["demo", "mi proyecto", "a/b", "a\\b", "proyecto-1_final", "módulo-ñ", "./demo"])
def test_resolver_accepts_normal_names(tmp_path: Path, buena: str) -> None:
    destino = resolver(tmp_path, buena)
    assert destino.is_relative_to(tmp_path.resolve())


def test_resolver_allows_dotfiles_only_when_asked(tmp_path: Path) -> None:
    assert resolver(tmp_path, ".gitignore", permitir_ocultos=True).name == ".gitignore"
    with pytest.raises(RutaNoPermitida):
        resolver(tmp_path, ".gitignore")
    with pytest.raises(RutaNoPermitida):
        resolver(tmp_path, "../x", permitir_ocultos=True)


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError, NotImplementedError:
        if os.name == "nt":
            import subprocess

            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False)
            if r.returncode == 0:
                return
        pytest.skip("No se pueden crear enlaces simbólicos ni uniones aquí")


def test_symlink_or_junction_escaping_the_root_is_rejected(tmp_path: Path) -> None:
    raiz = tmp_path / "raiz"
    fuera = tmp_path / "fuera"
    raiz.mkdir()
    fuera.mkdir()
    _symlink_or_skip(raiz / "atajo", fuera)
    with pytest.raises(RutaNoPermitida):
        resolver(raiz, "atajo")
    with pytest.raises(RutaNoPermitida):
        resolver(raiz, "atajo/secreto.txt")


def test_validar_raiz_rejects_wide_roots(tmp_path: Path) -> None:
    home = tmp_path / "usuario"
    home.mkdir()
    for ancha in (Path(home.anchor), home, tmp_path, home / ".ssh", home / ".praxis" / "x"):
        with pytest.raises(RutaNoPermitida):
            validar_raiz(ancha, home=home)
    assert validar_raiz(home / "Praxis", home=home) == (home / "Praxis").resolve()


# ---------- por la API ----------


def test_workspace_create_and_select(runner: Runner) -> None:
    h = runner.auth()
    r = runner.client.post("/workspace", json={"carpeta": "mi-proyecto", "crear": True}, headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["creada"] is True
    assert data["carpeta"] == "mi-proyecto"
    assert (runner.root / "mi-proyecto").is_dir()
    assert Path(data["ruta"]) == runner.root / "mi-proyecto"
    # Persiste: un State nuevo sobre el mismo fichero la recuerda.
    assert State(runner.config.state_file).workspace == "mi-proyecto"
    again = runner.client.post("/workspace", json={"carpeta": "mi-proyecto"}, headers=h)
    assert again.status_code == 200
    assert again.json()["creada"] is False


def test_workspace_missing_folder_needs_crear(runner: Runner) -> None:
    r = runner.client.post("/workspace", json={"carpeta": "no-existe"}, headers=runner.auth())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "carpeta_no_existe"
    assert not (runner.root / "no-existe").exists()


@pytest.mark.parametrize("mala", ["..", "../fuera", "C:\\Windows", "/etc", ".ssh", "a/../../x"])
def test_workspace_rejects_paths_outside_root(runner: Runner, mala: str) -> None:
    h = runner.auth()
    r = runner.client.post("/workspace", json={"carpeta": mala, "crear": True}, headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ruta_no_permitida"
    assert runner.state.workspace is None
    assert not (runner.root.parent / "fuera").exists()


def test_workspace_cannot_choose_a_file(runner: Runner) -> None:
    (runner.root / "fichero.txt").write_text("x", encoding="utf-8")
    r = runner.client.post("/workspace", json={"carpeta": "fichero.txt"}, headers=runner.auth())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "no_es_carpeta"


def test_browser_cannot_change_the_root(runner: Runner) -> None:
    h = runner.auth()
    for cuerpo in ({"carpeta": "x", "raiz": "C:\\"}, {"raiz": "/"}, {"carpeta": "x", "root": "/"}):
        assert runner.client.post("/workspace", json=cuerpo, headers=h).status_code == 422
    assert runner.state.root == runner.root


def test_changing_root_forgets_workspace(runner: Runner, tmp_path: Path) -> None:
    runner.workspace("demo")
    nueva = tmp_path / "otra"
    nueva.mkdir()
    runner.state.set_root(nueva.resolve())
    assert runner.state.workspace is None


def test_workspace_that_escapes_later_is_ignored(runner: Runner, tmp_path: Path) -> None:
    """Si la carpeta elegida se sustituye por un enlace que sale de la raíz, deja de contar como carpeta de trabajo."""
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    runner.state.set_workspace("atajo")
    _symlink_or_skip(runner.root / "atajo", fuera)
    r = runner.client.get("/workspace", headers=runner.auth())
    assert r.json()["carpeta"] is None
    assert runner.client.post("/test", json={}, headers=runner.auth()).status_code == 409
