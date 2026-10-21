"""Configuración del runner: puerto, orígenes permitidos y carpeta de estado.

Todo se lee de variables de entorno con valores por defecto seguros:

- PRAXIS_RUNNER_PORT: puerto en 127.0.0.1 (por defecto 47821, especificación §6.9).
- PRAXIS_RUNNER_ORIGINS: orígenes extra separados por comas (p. ej. una beta en otra dirección).
- PRAXIS_RUNNER_HOME: carpeta del estado del runner (por defecto ~/.praxis).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 47821
HOST = "127.0.0.1"

# Orígenes de la PWA publicada: la beta (Worker «praxis») y el desarrollo (Worker «praxis-dev»,
# docs/OPERACION.md). Los de desarrollo local (localhost y 127.0.0.1, cualquier puerto) van aparte.
APP_ORIGIN = "https://praxis.gonzaloenbolsa2021.workers.dev"
DEV_ORIGIN = "https://praxis-dev.gonzaloenbolsa2021.workers.dev"
DEFAULT_ORIGINS = (APP_ORIGIN, DEV_ORIGIN)

# Servidores de desarrollo: http://localhost:8010, http://127.0.0.1:8000...
LOCAL_ORIGIN_RE = re.compile(r"^http://(?:localhost|127\.0\.0\.1)(?::\d{1,5})?$")

MAX_BODY_BYTES = 256 * 1024  # tope de cuerpo de las peticiones


def default_home() -> Path:
    return Path.home() / ".praxis"


def default_root() -> Path:
    """Carpeta raíz de trabajo por defecto si el alumno no elige otra."""
    return Path.home() / "Praxis"


@dataclass(frozen=True)
class Config:
    port: int = DEFAULT_PORT
    origins: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_ORIGINS))
    home: Path = field(default_factory=default_home)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ if env is None else env)
        port = int(env.get("PRAXIS_RUNNER_PORT", DEFAULT_PORT))
        if not 1024 <= port <= 65535:
            raise ValueError(f"Puerto fuera de rango: {port}")
        extra = {o.strip().rstrip("/") for o in env.get("PRAXIS_RUNNER_ORIGINS", "").split(",")}
        if "*" in extra:
            raise ValueError("PRAXIS_RUNNER_ORIGINS no admite '*': lista los orígenes uno a uno.")
        origins = frozenset(DEFAULT_ORIGINS) | {o for o in extra if o}
        home_env = env.get("PRAXIS_RUNNER_HOME")
        return cls(port=port, origins=origins, home=Path(home_env) if home_env else default_home())

    @property
    def state_file(self) -> Path:
        return self.home / "runner_state.json"

    def allowed_hosts(self) -> frozenset[str]:
        """Valores aceptados en la cabecera Host (defensa contra DNS rebinding)."""
        return frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})

    def origin_allowed(self, origin: str | None) -> bool:
        origin = (origin or "").rstrip("/")
        return bool(origin) and (origin in self.origins or bool(LOCAL_ORIGIN_RE.fullmatch(origin)))
