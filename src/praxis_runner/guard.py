"""Middleware ASGI de seguridad: Host, Origin, CORS, Private Network Access y límites de cuerpo.

Va por fuera de FastAPI para que ninguna ruta, ni siquiera un 404, se salte las comprobaciones:

- Host: solo 127.0.0.1:PUERTO o localhost:PUERTO (defensa contra DNS rebinding).
- Origin: si viene, tiene que ser el de la PWA o un servidor de desarrollo local; nunca `*`.
- POST: solo `application/json` (obliga al navegador a hacer preflight, que solo contestamos a
  orígenes permitidos) y con Content-Length acotado.
- Preflight de Chrome para red privada/local: `Access-Control-Allow-Private-Network` y `-Local-Network`.
"""

from __future__ import annotations

import json

from .config import MAX_BODY_BYTES, Config

SECURITY_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"cross-origin-resource-policy", b"cross-origin"),  # la PWA tiene COEP require-corp
]


class Guard:
    def __init__(self, app, config: Config) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        origin = headers.get("origin", "").rstrip("/")
        origin_ok = self.config.origin_allowed(origin)
        cors = [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")] if origin_ok else []

        if headers.get("host", "") not in self.config.allowed_hosts():
            await self._error(send, 403, "bad_host", "Cabecera Host no permitida.", cors)
            return
        if origin and not origin_ok:
            await self._error(send, 403, "bad_origin", "Origen no permitido.", [])
            return

        method = scope["method"]
        if method == "OPTIONS":
            await self._preflight(send, headers, origin, origin_ok)
            return
        if method == "POST":
            media = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media != "application/json":
                await self._error(send, 415, "tipo_no_soportado", "El cuerpo debe ser application/json.", cors)
                return
            length = headers.get("content-length")
            if length is None or not length.isdigit():
                await self._error(send, 411, "falta_longitud", "Falta Content-Length.", cors)
                return
            if int(length) > MAX_BODY_BYTES:
                await self._error(send, 413, "cuerpo_demasiado_grande", "La petición es demasiado grande.", cors)
                return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                nombres = {k.lower() for k, _ in message["headers"]}
                extra = [h for h in [*SECURITY_HEADERS, *cors] if h[0] not in nombres]
                message = {**message, "headers": [*message["headers"], *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    async def _preflight(self, send, headers: dict[str, str], origin: str, origin_ok: bool) -> None:
        if not origin_ok:
            await self._error(send, 403, "bad_origin", "Origen no permitido.", [])
            return
        out = [
            (b"access-control-allow-origin", origin.encode()),
            (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
            (b"access-control-allow-headers", b"Authorization, Content-Type"),
            (b"access-control-max-age", b"600"),
            (b"vary", b"Origin"),
            (b"content-length", b"0"),
            *SECURITY_HEADERS,
        ]
        # Chrome (Private/Local Network Access) lo pide en el preflight hacia loopback.
        if headers.get("access-control-request-private-network") == "true":
            out.append((b"access-control-allow-private-network", b"true"))
        if headers.get("access-control-request-local-network") == "true":
            out.append((b"access-control-allow-local-network", b"true"))
        await send({"type": "http.response.start", "status": 204, "headers": out})
        await send({"type": "http.response.body", "body": b""})

    async def _error(self, send, status: int, code: str, message: str, cors: list[tuple[bytes, bytes]]) -> None:
        body = json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            *SECURITY_HEADERS,
            *cors,
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
