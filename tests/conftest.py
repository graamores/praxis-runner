"""Fixtures comunes: un runner completo en memoria (FastAPI + TestClient) sobre carpetas temporales."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from praxis_runner import ejecucion_tests, herramientas
from praxis_runner.app import create_app
from praxis_runner.config import APP_ORIGIN, Config
from praxis_runner.pairing import Pairing
from praxis_runner.state import State

PORT = 47821
BASE = f"http://127.0.0.1:{PORT}"


@dataclass
class Runner:
    config: Config
    state: State
    pairing: Pairing
    client: TestClient
    root: Path
    announced: list[str] = field(default_factory=list)

    def pair(self, origin: str | None = APP_ORIGIN) -> str:
        """Empareja con el código vigente y devuelve el token."""
        headers = {"Origin": origin} if origin else {}
        r = self.client.post("/pair", json={"codigo": self.pairing.code}, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def auth(self, token: str | None = None, origin: str | None = APP_ORIGIN) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token or self.pair(origin)}"}
        if origin:
            headers["Origin"] = origin
        return headers

    def workspace(self, name: str = "demo") -> Path:
        """Crea y elige una carpeta de trabajo; devuelve su ruta."""
        carpeta = self.root / name
        carpeta.mkdir(parents=True, exist_ok=True)
        self.state.set_workspace(name)
        return carpeta


@pytest.fixture
def runner(tmp_path: Path) -> Runner:
    config = Config(port=PORT, home=tmp_path / "home")
    state = State(config.state_file)
    root = tmp_path / "Praxis"
    root.mkdir()
    state.set_root(root.resolve())
    announced: list[str] = []
    pairing = Pairing(announced.append)
    client = TestClient(create_app(config, state, pairing), base_url=BASE)
    return Runner(config, state, pairing, client, root.resolve(), announced)


@pytest.fixture(autouse=True)
def _no_real_tool_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /hello no debe arrancar VS Code ni uv en cada test; las pruebas de detección real van aparte."""
    herramientas.limpiar_cache()
    monkeypatch.setattr(
        "praxis_runner.app.detectar_todas",
        lambda: {
            n: {"instalado": n == "python", "version": "3.14.2" if n == "python" else None}
            for n in herramientas.HERRAMIENTAS
        },
    )


@pytest.fixture
def usa_este_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los tests del alumno se ejecutan con el Python de este entorno (que tiene pytest)."""
    monkeypatch.setattr(
        ejecucion_tests,
        "comando_tests",
        lambda cwd: [sys.executable, "-m", *ejecucion_tests.PYTEST_FLAGS],
    )
