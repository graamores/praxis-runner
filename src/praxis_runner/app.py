"""API v1 del runner (FastAPI).

Rutas: GET /health (pública), POST /pair, GET /version, GET /hello, GET|POST /workspace,
POST /check, POST /test, POST /unpair. Todo salvo /health y /pair exige `Authorization: Bearer`.
"""

from __future__ import annotations

import hmac
import platform
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import API_VERSION, __version__
from .checks import PeticionCheck, ejecutar_comprobaciones
from .config import Config
from .ejecucion_tests import DEFAULT_TIMEOUT, MAX_TIMEOUT, ejecutar_tests
from .guard import Guard
from .herramientas import detectar_todas
from .pairing import PairError, Pairing
from .state import State, TokenInfo
from .workspace import RutaNoPermitida, listar_carpetas, relativa, resolver


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codigo: str = Field(min_length=1, max_length=32)


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carpeta: str = Field(min_length=1, max_length=200)  # relativa a la raíz, p. ej. "mi-proyecto"
    crear: bool = False


class PeticionTests(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_s: float = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)


@dataclass
class Auth:
    token: str
    info: TokenInfo


def require_token(request: Request) -> Auth:
    """Dependencia de las rutas protegidas: Bearer válido y, si hay Origin, el mismo con el que se emparejó."""
    state: State = request.app.state.praxis_state
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    value = value.strip()
    info = state.find_token(value) if scheme.lower() == "bearer" and value else None
    if info is None:
        raise ApiError(401, "unauthorized", "Falta el token o no es válido. Vuelve a emparejar el runner.")
    origin = request.headers.get("origin", "").rstrip("/")
    if origin and info.origin and not hmac.compare_digest(origin, info.origin):
        raise ApiError(403, "origen_distinto", "Este token se emparejó desde otro origen.")
    return Auth(value, info)


Autenticado = Annotated[Auth, Depends(require_token)]


def create_app(config: Config, state: State, pairing: Pairing) -> FastAPI:
    app = FastAPI(
        title="praxis-runner",
        version=__version__,
        docs_url=None,  # sin /docs ni /openapi.json: la superficie es solo la API v1
        redoc_url=None,
        openapi_url=None,
    )
    app.state.praxis_state = state
    busy = threading.Lock()  # una comprobación o ejecución de tests a la vez

    # ---------- errores con una sola forma: {"error": {"code", "message"}} ----------
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(PairError)
    async def _pair_error(_: Request, exc: PairError) -> JSONResponse:
        return _error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return _error_response(422, "peticion_invalida", f"Petición no válida ({where}): {first.get('msg', '')}"[:300])

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "metodo_no_permitido"}.get(exc.status_code, "error")
        return _error_response(
            exc.status_code, code, "Ruta desconocida." if exc.status_code == 404 else str(exc.detail)
        )

    def workspace_dir() -> Path | None:
        rel, root = state.workspace, state.root
        if not rel or root is None:
            return None
        try:
            ruta = resolver(root, rel)
        except RutaNoPermitida:
            return None
        return ruta if ruta.is_dir() else None

    def workspace_payload() -> dict:
        root, rel = state.root, state.workspace
        carpeta = workspace_dir()
        return {
            "raiz": str(root) if root else None,
            "carpeta": rel if carpeta else None,
            "ruta": str(carpeta) if carpeta else None,
            "carpetas": listar_carpetas(root) if root and root.is_dir() else [],
        }

    # ---------- rutas públicas ----------
    @app.api_route("/health", methods=["GET", "HEAD"])
    def health() -> dict:
        return {"status": "ok", "name": "praxis-runner", "api": API_VERSION}

    @app.post("/pair")
    def pair(body: PairRequest, request: Request) -> dict:
        pairing.verify(body.codigo)
        token = state.issue_token(request.headers.get("origin", "").rstrip("/"))
        return {"token": token, "api": API_VERSION, "version": __version__}

    # ---------- rutas con token ----------
    @app.get("/version")
    def version(_: Autenticado) -> dict:
        return {
            "name": "praxis-runner",
            "version": __version__,
            "api": API_VERSION,
            "python": platform.python_version(),
            "os": platform.system(),
        }

    @app.get("/hello")
    def hello(_: Autenticado) -> dict:
        return {
            "runner": {"nombre": "praxis-runner", "version": __version__, "api": API_VERSION},
            "so": {"sistema": platform.system(), "version": platform.release()},
            "herramientas": detectar_todas(),
            "espacio": workspace_payload(),
        }

    @app.post("/unpair")
    def unpair(auth: Autenticado) -> dict:
        state.revoke_token(auth.token)
        return {"ok": True}

    @app.get("/workspace")
    def get_workspace(_: Autenticado) -> dict:
        return workspace_payload()

    @app.post("/workspace")
    def set_workspace(body: WorkspaceRequest, _: Autenticado) -> dict:
        root = state.root
        if root is None:
            raise ApiError(
                409, "sin_raiz", "El runner no tiene carpeta raíz. Arráncalo de nuevo o ejecuta «praxis-runner root»."
            )
        try:
            destino = resolver(root, body.carpeta)
        except RutaNoPermitida as e:
            raise ApiError(400, "ruta_no_permitida", str(e)) from e
        creada = False
        if not destino.exists():
            if not body.crear:
                raise ApiError(404, "carpeta_no_existe", f"No existe «{body.carpeta}» dentro de tu raíz.")
            try:
                destino.mkdir(parents=True)
            except OSError as e:
                raise ApiError(500, "no_se_pudo_crear", f"No pude crear la carpeta: {e.strerror}") from e
            creada = True
        elif not destino.is_dir():
            raise ApiError(400, "no_es_carpeta", f"«{body.carpeta}» existe pero no es una carpeta.")
        state.set_workspace(relativa(root, destino))
        return {**workspace_payload(), "creada": creada}

    @app.post("/check")
    def check(body: PeticionCheck, _: Autenticado) -> dict:
        if not busy.acquire(blocking=False):
            raise ApiError(409, "ocupado", "Ya hay una comprobación en marcha. Espera a que termine.")
        try:
            resultados = ejecutar_comprobaciones(list(body.comprobaciones), workspace_dir())
        finally:
            busy.release()
        return {"resultados": resultados, "todo_ok": all(r["ok"] for r in resultados)}

    @app.post("/test")
    def run_tests(body: PeticionTests, _: Autenticado) -> dict:
        carpeta = workspace_dir()
        if carpeta is None:
            raise ApiError(409, "sin_carpeta", "Elige primero tu carpeta de trabajo (POST /workspace).")
        if not busy.acquire(blocking=False):
            raise ApiError(409, "ocupado", "Ya hay una comprobación en marcha. Espera a que termine.")
        try:
            return ejecutar_tests(carpeta, body.timeout_s)
        finally:
            busy.release()

    app.add_middleware(Guard, config=config)
    return app
