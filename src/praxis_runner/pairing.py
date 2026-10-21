"""Emparejamiento con código de un solo uso.

El runner imprime en su consola un código de 8 dígitos. La PWA lo envía a POST /pair y recibe un
token de 256 bits. El código caduca a los 10 minutos, se gasta al usarse y se anula tras 5 fallos
(con 15 s de bloqueo): adivinarlo no es viable y quien no ve la consola no puede emparejarse.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable

TTL_SECONDS = 600
MAX_FAILS = 5
LOCKOUT_SECONDS = 15


class PairError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def format_code(code: str) -> str:
    return f"{code[:4]} {code[4:]}"


class Pairing:
    def __init__(
        self,
        announce: Callable[[str], None] | None = None,
        *,
        ttl: float = TTL_SECONDS,
        max_fails: int = MAX_FAILS,
        lockout: float = LOCKOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._announce = announce or (lambda code: None)
        self._ttl = ttl
        self._max_fails = max_fails
        self._lockout = lockout
        self._clock = clock
        self._lock = threading.Lock()
        self._code = ""
        self._expires = 0.0
        self._fails = 0
        self._locked_until = 0.0
        self.rotate()

    @property
    def code(self) -> str:
        """Código vigente (solo para la consola y los tests)."""
        with self._lock:
            return self._code

    def rotate(self) -> str:
        with self._lock:
            return self._rotate_locked()

    def _rotate_locked(self) -> str:
        self._code = f"{secrets.randbelow(10**8):08d}"
        self._expires = self._clock() + self._ttl
        self._fails = 0
        code = self._code
        self._announce(code)
        return code

    def verify(self, candidate: str) -> None:
        """Gasta el código si coincide; si no, lanza PairError."""
        with self._lock:
            now = self._clock()
            if now < self._locked_until:
                raise PairError("demasiados_intentos", "Demasiados intentos. Espera unos segundos.", 429)
            if now >= self._expires:
                self._rotate_locked()
                raise PairError("codigo_caducado", "El código caducó. Mira la consola: hay uno nuevo.", 410)
            digits = re.sub(r"[\s-]", "", candidate or "")
            if re.fullmatch(r"\d{8}", digits) and hmac.compare_digest(digits, self._code):
                self._rotate_locked()  # un solo uso; el nuevo sirve para emparejar otro navegador
                return
            self._fails += 1
            if self._fails >= self._max_fails:
                self._locked_until = now + self._lockout
                self._rotate_locked()
                raise PairError(
                    "codigo_anulado",
                    "Demasiados fallos: el código se ha anulado. Mira la consola: hay uno nuevo.",
                    429,
                )
            raise PairError("codigo_incorrecto", "El código no coincide. Míralo en la consola del runner.", 403)
