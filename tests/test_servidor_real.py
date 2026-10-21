"""El runner de verdad: `python -m praxis_runner` con uvicorn, sobre un puerto libre y un estado temporal."""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from praxis_runner.config import APP_ORIGIN
from praxis_runner.state import State


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Vivo:
    port: int
    code: str
    home: Path
    proc: subprocess.Popen

    def request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body) if body is not None else None
        hdrs = {"Content-Type": "application/json", **(headers or {})} if data else dict(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, json.loads(raw) if raw else None


@pytest.fixture
def vivo(tmp_path: Path) -> Iterator[Vivo]:
    home = tmp_path / "home"
    root = tmp_path / "Praxis"
    root.mkdir()
    State(home / "runner_state.json").set_root(root.resolve())
    port = _free_port()
    env = {**os.environ, "PRAXIS_RUNNER_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "praxis_runner", "--port", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        encoding="utf-8",
    )
    lines: list[str] = []
    found = threading.Event()
    code: dict[str, str] = {}

    def drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            m = re.search(r"Código de emparejamiento:\s+(\d{4}) (\d{4})", line)
            if m:
                code["v"] = m.group(1) + m.group(2)
                found.set()

    threading.Thread(target=drain, daemon=True).start()
    assert found.wait(30), "el runner no imprimió el código de emparejamiento:\n" + "".join(lines)
    for _ in range(50):  # uvicorn tarda un instante más en aceptar conexiones
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    yield Vivo(port, code["v"], home, proc)
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_real_server_pairs_and_answers(vivo: Vivo) -> None:
    status, headers, body = vivo.request("GET", "/health", headers={"Origin": APP_ORIGIN})
    assert status == 200
    assert body["name"] == "praxis-runner"
    assert headers["access-control-allow-origin"] == APP_ORIGIN
    assert "server" not in headers  # sin «uvicorn» anunciado

    status, _, body = vivo.request("GET", "/hello")
    assert status == 401

    status, _, body = vivo.request("POST", "/pair", {"codigo": vivo.code}, {"Origin": APP_ORIGIN})
    assert status == 200
    token = body["token"]

    status, _, body = vivo.request("GET", "/hello", headers={"Authorization": f"Bearer {token}", "Origin": APP_ORIGIN})
    assert status == 200
    assert body["herramientas"]["python"]["instalado"] is True
    assert body["espacio"]["raiz"] is not None

    # El token queda guardado en disco solo como hash y sobrevive a un reinicio.
    estado = (vivo.home / "runner_state.json").read_text(encoding="utf-8")
    assert token not in estado


def test_real_server_rejects_bad_host_and_origin(vivo: Vivo) -> None:
    status, _, body = vivo.request("GET", "/health", headers={"Host": "evil.example"})
    assert status == 403
    assert body["error"]["code"] == "bad_host"
    status, _, body = vivo.request("GET", "/health", headers={"Origin": "https://evil.example"})
    assert status == 403
    assert body["error"]["code"] == "bad_origin"


def test_real_server_listens_on_loopback_only(vivo: Vivo) -> None:
    # Ni por IPv6 ni por la dirección de la red local.
    with pytest.raises(OSError), socket.create_connection(("::1", vivo.port), timeout=1):
        pass
    try:
        lan = socket.gethostbyname(socket.gethostname())
    except OSError:
        pytest.skip("no se pudo resolver la IP de la red local")
    if lan.startswith("127."):
        pytest.skip("la máquina no tiene otra IP que loopback")
    with pytest.raises(OSError), socket.create_connection((lan, vivo.port), timeout=1):
        pass


def test_second_instance_on_same_port_refuses_to_start(vivo: Vivo) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "praxis_runner", "--port", str(vivo.port)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "PRAXIS_RUNNER_HOME": str(vivo.home), "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    assert r.returncode == 1
    assert "ocupado" in r.stdout
