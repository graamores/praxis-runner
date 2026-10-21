"""Línea de órdenes: `praxis-runner` arranca el servidor y enseña el código de emparejamiento.

praxis-runner              arranca (por defecto)
praxis-runner root [RUTA]  muestra o cambia la carpeta raíz de trabajo
praxis-runner status       carpeta raíz, carpeta de trabajo y navegadores emparejados
praxis-runner unpair       olvida todos los navegadores emparejados
"""

from __future__ import annotations

import argparse
import contextlib
import socket
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import HOST, Config, default_root
from .pairing import Pairing, format_code
from .state import State
from .workspace import RutaNoPermitida, validar_raiz


def _say(text: str = "") -> None:
    print(text, flush=True)


def choose_root(state: State, *, interactive: bool) -> Path:
    """Devuelve la raíz guardada o la pide una vez (Enter acepta la propuesta)."""
    actual = state.root
    if actual is not None:
        return actual
    propuesta = default_root()
    elegida = propuesta
    if interactive:
        _say("Primera vez: elige la carpeta donde vivirán tus proyectos de Praxis.")
        _say("El runner nunca saldrá de ella. Puedes cambiarla luego con: praxis-runner root RUTA")
        while True:
            try:
                respuesta = input(f"Carpeta raíz [{propuesta}]: ").strip().strip('"')
            except EOFError:
                _say()
                respuesta = ""
            try:
                elegida = Path(respuesta) if respuesta else propuesta
                validar_raiz(elegida)
                break
            except RutaNoPermitida as e:
                _say(f"  No vale: {e}")
    raiz = validar_raiz(elegida)
    raiz.mkdir(parents=True, exist_ok=True)
    state.set_root(raiz)
    return raiz


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def serve(config: Config, state: State) -> int:
    import uvicorn

    from .app import create_app

    if not _port_free(config.port):
        _say(f"El puerto {config.port} está ocupado. ¿Ya tienes otro praxis-runner abierto?")
        return 1
    raiz = choose_root(state, interactive=sys.stdin is not None and sys.stdin.isatty())

    def announce(code: str) -> None:
        _say()
        _say(f"  Código de emparejamiento:  {format_code(code)}")
        _say("  (un solo uso; caduca a los 10 minutos; escríbelo en Praxis > Misiones > Taller)")
        _say()

    _say(f"praxis-runner {__version__}")
    _say(f"Escuchando en http://{HOST}:{config.port} (solo este ordenador)")
    _say(f"Carpeta raíz: {raiz}")
    _say(f"Navegadores emparejados: {state.token_count()}")
    pairing = Pairing(announce)
    _say("Ctrl+C para parar.")
    app = create_app(config, state, pairing)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=config.port,
            log_level="warning",
            access_log=False,
            server_header=False,
            date_header=False,
            proxy_headers=False,
            ws="none",
            timeout_keep_alive=5,
        )
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.run()
    _say("\nParado.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="praxis-runner", description="Runner local de Praxis.")
    parser.add_argument("--version", action="version", version=f"praxis-runner {__version__}")
    parser.add_argument("--port", type=int, help="puerto (por defecto 47821 o PRAXIS_RUNNER_PORT)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="arranca el servidor (orden por defecto)")
    root = sub.add_parser("root", help="muestra o cambia la carpeta raíz de trabajo")
    root.add_argument("path", nargs="?", help="nueva carpeta raíz (se crea si no existe)")
    sub.add_parser("status", help="estado: raíz, carpeta de trabajo y navegadores emparejados")
    sub.add_parser("unpair", help="olvida todos los navegadores emparejados")
    args = parser.parse_args(argv)

    config = Config.from_env()
    if args.port:
        config = replace(config, port=args.port)
    state = State(config.state_file)

    if args.command == "root":
        if args.path:
            try:
                nueva = validar_raiz(Path(args.path))
            except RutaNoPermitida as e:
                _say(f"No vale esa carpeta: {e}")
                return 1
            nueva.mkdir(parents=True, exist_ok=True)
            state.set_root(nueva)
            _say(f"Carpeta raíz: {nueva}")
        else:
            _say(f"Carpeta raíz: {state.root or '(sin elegir: se pregunta al arrancar)'}")
        return 0
    if args.command == "status":
        _say(f"Carpeta raíz: {state.root or '(sin elegir)'}")
        _say(f"Carpeta de trabajo: {state.workspace or '(sin elegir)'}")
        _say(f"Navegadores emparejados: {state.token_count()}")
        _say(f"Estado en: {config.state_file}")
        return 0
    if args.command == "unpair":
        _say(f"Olvidados {state.revoke_all()} navegadores emparejados.")
        return 0
    return serve(config, state)


if __name__ == "__main__":
    sys.exit(main())
