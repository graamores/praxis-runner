"""Configuración, estado, emparejamiento (con reloj falso), detección de herramientas y órdenes de la CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from praxis_runner import herramientas
from praxis_runner.cli import choose_root, main
from praxis_runner.config import APP_ORIGIN, DEFAULT_PORT, DEV_ORIGIN, Config
from praxis_runner.pairing import PairError, Pairing, format_code
from praxis_runner.state import MAX_TOKENS, State

# ---------- configuración ----------


def test_config_defaults() -> None:
    config = Config.from_env({})
    assert config.port == DEFAULT_PORT
    assert config.origins == {APP_ORIGIN, DEV_ORIGIN}  # beta y desarrollo (docs/OPERACION.md)
    assert APP_ORIGIN == "https://praxis.gonzaloenbolsa2021.workers.dev"


def test_config_extra_origins_port_and_home(tmp_path: Path) -> None:
    env = {
        "PRAXIS_RUNNER_PORT": "50000",
        "PRAXIS_RUNNER_ORIGINS": "https://beta.example.dev/, ,https://otra.example.dev",
        "PRAXIS_RUNNER_HOME": str(tmp_path / "h"),
    }
    config = Config.from_env(env)
    assert config.port == 50000
    assert {"https://beta.example.dev", "https://otra.example.dev"} <= config.origins
    assert "" not in config.origins
    assert config.home == tmp_path / "h"
    assert config.state_file == tmp_path / "h" / "runner_state.json"
    assert config.allowed_hosts() == {"127.0.0.1:50000", "localhost:50000"}


def test_config_rejects_bad_port_and_star_origin() -> None:
    with pytest.raises(ValueError):
        Config.from_env({"PRAXIS_RUNNER_PORT": "80"})
    with pytest.raises(ValueError):
        Config.from_env({"PRAXIS_RUNNER_ORIGINS": "*"})


def test_origin_allowed_rules() -> None:
    config = Config()
    assert config.origin_allowed(APP_ORIGIN + "/")
    assert config.origin_allowed("http://localhost:9999")
    assert not config.origin_allowed("")
    assert not config.origin_allowed(None)
    assert not config.origin_allowed("http://localhost:99999999")
    assert not config.origin_allowed("http://localhost:8000/path")


# ---------- estado ----------


def test_state_roundtrip_and_token_cap(tmp_path: Path) -> None:
    state = State(tmp_path / "s.json")
    tokens = [state.issue_token("https://x.dev") for _ in range(MAX_TOKENS + 2)]
    assert state.token_count() == MAX_TOKENS
    assert state.find_token(tokens[0]) is None  # los más antiguos se descartan
    assert state.find_token(tokens[-1]) is not None
    assert State(tmp_path / "s.json").token_count() == MAX_TOKENS
    assert state.revoke_all() == MAX_TOKENS
    assert state.find_token(tokens[-1]) is None


def test_state_ignores_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{no es json", encoding="utf-8")
    state = State(path)
    assert state.root is None
    assert state.token_count() == 0


# ---------- emparejamiento ----------


class Reloj:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_code_expires_and_is_replaced() -> None:
    reloj = Reloj()
    anunciados: list[str] = []
    pairing = Pairing(anunciados.append, ttl=600, clock=reloj)
    primero = pairing.code
    reloj.t += 601
    with pytest.raises(PairError) as e:
        pairing.verify(primero)
    assert e.value.code == "codigo_caducado"
    assert pairing.code != primero or len(anunciados) == 2
    assert len(anunciados) == 2
    pairing.verify(pairing.code)  # el nuevo funciona


def test_code_has_eight_digits_and_formats() -> None:
    pairing = Pairing()
    assert len(pairing.code) == 8
    assert pairing.code.isdigit()
    assert format_code("12345678") == "1234 5678"


def test_lockout_ends_after_a_while() -> None:
    reloj = Reloj()
    pairing = Pairing(clock=reloj, max_fails=2, lockout=15)
    for _ in range(2):
        with pytest.raises(PairError):
            pairing.verify("00000000" if pairing.code != "00000000" else "11111111")
    with pytest.raises(PairError) as e:
        pairing.verify(pairing.code)
    assert e.value.code == "demasiados_intentos"
    reloj.t += 16
    pairing.verify(pairing.code)


def test_codes_are_random() -> None:
    assert len({Pairing().code for _ in range(20)}) > 15


# ---------- detección de herramientas ----------


def test_detect_unknown_executable_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        herramientas.HERRAMIENTAS, "uv", ([["programa-que-no-existe-praxis", "--version"]], r"^uv [0-9]")
    )
    assert herramientas.detectar("uv") == {"instalado": False, "version": None}


def test_detect_rejects_output_without_the_tool_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    # Como el python.exe de la Tienda de Microsoft: sale con 0 pero no imprime una versión.
    monkeypatch.setitem(
        herramientas.HERRAMIENTAS, "python", ([[sys.executable, "-c", "print('Ve a la Tienda')"]], r"^Python [0-9]")
    )
    assert herramientas.detectar("python")["instalado"] is False


def test_detect_python_for_real() -> None:
    info = herramientas.detectar("python")
    assert info["instalado"] is True
    assert info["version"].startswith("3.")


# ---------- CLI ----------


def test_choose_root_default_non_interactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("praxis_runner.cli.default_root", lambda: tmp_path / "Praxis")
    state = State(tmp_path / "s.json")
    assert choose_root(state, interactive=False) == (tmp_path / "Praxis").resolve()
    assert (tmp_path / "Praxis").is_dir()
    assert state.root == (tmp_path / "Praxis").resolve()
    assert choose_root(state, interactive=False) == state.root  # no vuelve a preguntar


def test_choose_root_asks_until_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    respuestas = iter([str(Path(tmp_path.anchor)), str(tmp_path / "Mis proyectos")])
    monkeypatch.setattr("builtins.input", lambda _="": next(respuestas))
    state = State(tmp_path / "s.json")
    assert choose_root(state, interactive=True) == (tmp_path / "Mis proyectos").resolve()
    assert "No vale" in capsys.readouterr().out


def test_cli_root_status_unpair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("PRAXIS_RUNNER_HOME", str(tmp_path / "home"))
    destino = tmp_path / "Praxis"
    assert main(["root", str(destino)]) == 0
    assert destino.is_dir()
    assert main(["root", str(Path(tmp_path.anchor))]) == 1  # un disco entero no vale
    assert "No vale" in capsys.readouterr().out
    state = State(tmp_path / "home" / "runner_state.json")
    assert state.root == destino.resolve()
    state.issue_token("")
    assert main(["status"]) == 0
    assert "emparejados: 1" in capsys.readouterr().out
    assert main(["unpair"]) == 0
    assert State(tmp_path / "home" / "runner_state.json").token_count() == 0


def test_cli_version(capsys) -> None:
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "praxis-runner 0." in capsys.readouterr().out
