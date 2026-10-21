"""Seguridad de la API: token, emparejamiento, Host, Origin, CORS, Private Network Access y límites."""

from __future__ import annotations

import pytest
from conftest import PORT, Runner

from praxis_runner import API_VERSION, __version__
from praxis_runner.config import APP_ORIGIN, DEFAULT_ORIGINS, DEV_ORIGIN, Config

RUTAS_PROTEGIDAS = [
    ("GET", "/version"),
    ("GET", "/hello"),
    ("GET", "/workspace"),
    ("POST", "/workspace"),
    ("POST", "/check"),
    ("POST", "/test"),
    ("POST", "/unpair"),
]


# ---------- rutas públicas y rechazo sin token ----------


def test_health_is_public_and_minimal(runner: Runner) -> None:
    r = runner.client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "name": "praxis-runner", "api": API_VERSION}
    assert r.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in r.headers


def test_head_health_has_no_body(runner: Runner) -> None:
    r = runner.client.head("/health")
    assert r.status_code == 200
    assert r.content == b""


@pytest.mark.parametrize(("method", "path"), RUTAS_PROTEGIDAS)
def test_protected_routes_reject_missing_token(runner: Runner, method: str, path: str) -> None:
    r = runner.client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(("method", "path"), RUTAS_PROTEGIDAS)
def test_protected_routes_reject_wrong_token(runner: Runner, method: str, path: str) -> None:
    runner.pair()
    headers = {"Authorization": "Bearer " + "x" * 43}
    r = runner.client.request(method, path, headers=headers, json={} if method == "POST" else None)
    assert r.status_code == 401


def test_basic_auth_scheme_is_not_accepted(runner: Runner) -> None:
    token = runner.pair()
    r = runner.client.get("/version", headers={"Authorization": f"Basic {token}"})
    assert r.status_code == 401


def test_unknown_path_and_docs_are_not_exposed(runner: Runner) -> None:
    for path in ("/nada", "/docs", "/openapi.json", "/redoc"):
        r = runner.client.get(path)
        assert r.status_code == 404, path
        assert r.json()["error"]["code"] == "not_found"


def test_version_with_token(runner: Runner) -> None:
    r = runner.client.get("/version", headers=runner.auth())
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == __version__
    assert data["api"] == API_VERSION
    assert data["python"].startswith("3.")


# ---------- emparejamiento ----------


def test_pair_announces_code_at_startup(runner: Runner) -> None:
    assert len(runner.announced) == 1
    assert len(runner.announced[0]) == 8
    assert runner.announced[0].isdigit()


def test_pair_returns_256_bit_token_and_it_works(runner: Runner) -> None:
    r = runner.client.post("/pair", json={"codigo": runner.pairing.code})
    assert r.status_code == 200
    token = r.json()["token"]
    assert len(token) >= 43  # 32 bytes en base64 url-safe
    assert runner.client.get("/version", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_pair_accepts_code_with_space(runner: Runner) -> None:
    code = runner.pairing.code
    r = runner.client.post("/pair", json={"codigo": f"{code[:4]} {code[4:]}"})
    assert r.status_code == 200


def test_pair_code_is_single_use(runner: Runner) -> None:
    code = runner.pairing.code
    assert runner.client.post("/pair", json={"codigo": code}).status_code == 200
    assert runner.pairing.code != code  # se anuncia uno nuevo
    again = runner.client.post("/pair", json={"codigo": code})
    assert again.status_code == 403
    assert again.json()["error"]["code"] == "codigo_incorrecto"
    assert len(runner.announced) == 2


def test_pair_wrong_code_rejected_without_token(runner: Runner) -> None:
    r = runner.client.post("/pair", json={"codigo": "00000000" if runner.pairing.code != "00000000" else "11111111"})
    assert r.status_code == 403
    assert "token" not in r.text


def test_pair_locks_after_five_failures_and_rotates_code(runner: Runner) -> None:
    original = runner.pairing.code
    wrong = "11111111" if original != "11111111" else "22222222"
    codes = [runner.client.post("/pair", json={"codigo": wrong}).status_code for _ in range(5)]
    assert codes == [403, 403, 403, 403, 429]
    assert runner.pairing.code != original
    # Durante el bloqueo ni siquiera el código bueno vale.
    r = runner.client.post("/pair", json={"codigo": runner.pairing.code})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "demasiados_intentos"


def test_pair_rejects_malformed_bodies(runner: Runner) -> None:
    assert runner.client.post("/pair", json={}).status_code == 422
    assert runner.client.post("/pair", json={"codigo": "1" * 100}).status_code == 422
    assert runner.client.post("/pair", json={"codigo": "12345678", "extra": 1}).status_code == 422
    r = runner.client.post("/pair", content=b"no es json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "peticion_invalida"


def test_pair_non_numeric_code_is_just_wrong(runner: Runner) -> None:
    assert runner.client.post("/pair", json={"codigo": "abcdefgh"}).status_code == 403


def test_unpair_revokes_only_that_token(runner: Runner) -> None:
    a, b = runner.pair(), runner.pair()
    assert runner.client.post("/unpair", json={}, headers=runner.auth(a)).status_code == 200
    assert runner.client.get("/version", headers=runner.auth(a)).status_code == 401
    assert runner.client.get("/version", headers=runner.auth(b)).status_code == 200


def test_tokens_are_stored_hashed(runner: Runner) -> None:
    token = runner.pair()
    contenido = runner.config.state_file.read_text(encoding="utf-8")
    assert token not in contenido
    assert "hash" in contenido


def test_tokens_survive_restart(runner: Runner) -> None:
    from praxis_runner.state import State

    token = runner.pair()
    reabierto = State(runner.config.state_file)
    assert reabierto.find_token(token) is not None
    assert reabierto.find_token(token + "x") is None


def test_token_is_bound_to_pairing_origin(runner: Runner) -> None:
    token = runner.pair(origin=APP_ORIGIN)
    ok = runner.client.get("/version", headers={"Authorization": f"Bearer {token}", "Origin": APP_ORIGIN})
    assert ok.status_code == 200
    otro = runner.client.get(
        "/version", headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"}
    )
    assert otro.status_code == 403
    assert otro.json()["error"]["code"] == "origen_distinto"


# ---------- Host y Origin ----------


def test_bad_host_rejected_even_on_health(runner: Runner) -> None:
    r = runner.client.get("/health", headers={"Host": "evil.example:80"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "bad_host"


def test_dns_rebinding_with_right_port_but_other_name_rejected(runner: Runner) -> None:
    r = runner.client.get("/health", headers={"Host": f"evil.example:{PORT}"})
    assert r.status_code == 403


def test_localhost_host_accepted(runner: Runner) -> None:
    assert runner.client.get("/health", headers={"Host": f"localhost:{PORT}"}).status_code == 200


def test_wrong_port_in_host_rejected(runner: Runner) -> None:
    assert runner.client.get("/health", headers={"Host": "127.0.0.1:9999"}).status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        APP_ORIGIN,
        DEV_ORIGIN,
        "http://localhost:8000",
        "http://localhost:5173",
        "http://127.0.0.1:8010",
        "http://localhost",
    ],
)
def test_allowed_origin_gets_cors(runner: Runner, origin: str) -> None:
    r = runner.client.get("/health", headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert r.headers["vary"] == "Origin"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://evil.example",
        "https://localhost:8000",  # https en localhost no es el servidor de desarrollo
        "http://localhost.evil.example",
        "http://127.0.0.1.evil.example:8000",
        "https://praxis.gonzaloenbolsa2021.workers.dev.evil.example",
        "http://praxis.gonzaloenbolsa2021.workers.dev",  # solo https
        "https://praxis.pages.dev",  # H-02: subdominio de Pages de un tercero (estuvo en la lista)
        "https://praxis-dev.pages.dev",
        "https://otro.gonzaloenbolsa2021.workers.dev",  # otro Worker de la misma cuenta tampoco
        "https://praxis-dev.gonzaloenbolsa2021.workers.dev:8443",
        "http://[::1]:8000",
        "null",
        "*",
    ],
)
def test_unknown_origin_rejected_everywhere(runner: Runner, origin: str) -> None:
    for method, path in [("GET", "/health"), ("POST", "/pair"), ("OPTIONS", "/hello")]:
        r = runner.client.request(
            method, path, headers={"Origin": origin}, json={"codigo": "12345678"} if method == "POST" else None
        )
        assert r.status_code == 403, (method, path)
        assert "access-control-allow-origin" not in r.headers
        assert r.json()["error"]["code"] == "bad_origin"


def test_default_origins_are_exactly_beta_and_dev() -> None:
    """H-02 (docs/SEGURIDAD.md §8): fuera de localhost, solo la beta y el desarrollo de Praxis."""
    assert DEFAULT_ORIGINS == (
        "https://praxis.gonzaloenbolsa2021.workers.dev",
        "https://praxis-dev.gonzaloenbolsa2021.workers.dev",
    )
    assert Config().origins == frozenset(DEFAULT_ORIGINS)


def test_never_allow_origin_star(runner: Runner) -> None:
    r = runner.client.get("/health", headers={"Origin": APP_ORIGIN})
    assert r.headers["access-control-allow-origin"] != "*"


def test_preflight_private_network(runner: Runner) -> None:
    r = runner.client.options(
        "/hello",
        headers={
            "Origin": APP_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
            "Access-Control-Request-Private-Network": "true",
            "Access-Control-Request-Local-Network": "true",
        },
    )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == APP_ORIGIN
    assert r.headers["access-control-allow-private-network"] == "true"
    assert r.headers["access-control-allow-local-network"] == "true"
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
    assert "POST" in r.headers["access-control-allow-methods"]


def test_preflight_without_pna_request_has_no_pna_header(runner: Runner) -> None:
    r = runner.client.options("/hello", headers={"Origin": APP_ORIGIN, "Access-Control-Request-Method": "GET"})
    assert r.status_code == 204
    assert "access-control-allow-private-network" not in r.headers


def test_preflight_without_origin_rejected(runner: Runner) -> None:
    assert runner.client.options("/hello").status_code == 403


def test_errors_carry_cors_so_the_app_can_read_them(runner: Runner) -> None:
    r = runner.client.get("/version", headers={"Origin": APP_ORIGIN})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == APP_ORIGIN


# ---------- cuerpo de las peticiones ----------


def test_post_requires_json_content_type(runner: Runner) -> None:
    # text/plain es una petición «simple» que el navegador manda sin preflight: se rechaza.
    for tipo in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        r = runner.client.post(
            "/pair", content=b'{"codigo":"12345678"}', headers={"Content-Type": tipo, "Origin": APP_ORIGIN}
        )
        assert r.status_code == 415, tipo
    assert (
        runner.client.post(
            "/pair", content=b"{}", headers={"Content-Type": "application/json; charset=utf-8"}
        ).status_code
        == 422
    )


def test_post_body_size_limit(runner: Runner) -> None:
    r = runner.client.post(
        "/check",
        content=b"{" + b" " * (300 * 1024) + b"}",
        headers={"Content-Type": "application/json", **runner.auth()},
    )
    assert r.status_code == 413


def test_security_headers_on_every_response(runner: Runner) -> None:
    r = runner.client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert "server" not in {k.lower() for k in r.headers}


# ---------- /hello ----------


def test_hello_reports_tools_and_workspace(runner: Runner) -> None:
    runner.workspace("mi-proyecto")
    r = runner.client.get("/hello", headers=runner.auth())
    assert r.status_code == 200
    data = r.json()
    assert data["runner"]["version"] == __version__
    assert set(data["herramientas"]) == {"python", "uv", "git", "code", "gh"}
    assert data["herramientas"]["python"] == {"instalado": True, "version": "3.14.2"}
    assert data["espacio"]["carpeta"] == "mi-proyecto"
    assert data["espacio"]["raiz"] == str(runner.root)
    assert "mi-proyecto" in data["espacio"]["carpetas"]
