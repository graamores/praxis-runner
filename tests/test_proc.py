"""Procesos con tiempo máximo, salida acotada y árbol matado entero."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from praxis_runner.proc import MAX_OUTPUT_BYTES, normalize_text, run_limited


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_runs_and_captures_output(tmp_path: Path) -> None:
    r = run_limited([sys.executable, "-c", "print('hola'); import sys; sys.stderr.write('adios')"], tmp_path, 20)
    assert r.code == 0
    assert "hola" in r.output
    assert "adios" in r.output
    assert not r.expired
    assert not r.truncated


def test_nonzero_exit_code(tmp_path: Path) -> None:
    assert run_limited([sys.executable, "-c", "raise SystemExit(3)"], tmp_path, 20).code == 3


def test_missing_executable_is_reported(tmp_path: Path) -> None:
    r = run_limited(["programa-que-no-existe-praxis"], tmp_path, 5)
    assert r.not_found
    assert r.code is None


def test_timeout_kills_the_process(tmp_path: Path) -> None:
    inicio = time.monotonic()
    r = run_limited([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, 1)
    assert r.expired
    assert r.code is None
    assert time.monotonic() - inicio < 15


def test_timeout_kills_grandchildren_too(tmp_path: Path) -> None:
    pidfile = tmp_path / "nieto.pid"
    nieto = f"import os,time,pathlib; pathlib.Path(r'{pidfile}').write_text(str(os.getpid())); time.sleep(60)"
    padre = f"import subprocess,sys,time\nsubprocess.Popen([sys.executable, '-c', {nieto!r}])\ntime.sleep(60)\n"
    r = run_limited([sys.executable, "-c", padre], tmp_path, 3)
    assert r.expired
    assert pidfile.exists(), "el nieto no llegó a arrancar; sube el tiempo del test"
    pid = int(pidfile.read_text())
    for _ in range(30):
        if not _alive(pid):
            break
        time.sleep(0.2)
    assert not _alive(pid), "el nieto sigue vivo tras el timeout"


def test_output_is_capped(tmp_path: Path) -> None:
    r = run_limited([sys.executable, "-c", "print('x' * 1_000_000)"], tmp_path, 20)
    assert r.code == 0
    assert r.truncated
    assert len(r.output.encode()) <= MAX_OUTPUT_BYTES


def test_child_gets_no_stdin(tmp_path: Path) -> None:
    r = run_limited([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], tmp_path, 10)
    assert "''" in r.output


def test_runner_venv_is_not_leaked_to_children(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "C:/venv-del-runner")
    r = run_limited([sys.executable, "-c", "import os; print(os.environ.get('VIRTUAL_ENV'))"], tmp_path, 10)
    assert "None" in r.output


def test_normalize_text_strips_ansi_and_crlf() -> None:
    assert normalize_text(b"\x1b[31mrojo\x1b[0m\r\nlinea\r") == "rojo\nlinea\n"
