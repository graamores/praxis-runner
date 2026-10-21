"""Estado persistente del runner: carpeta raíz, carpeta de trabajo y tokens emparejados.

Se guarda en JSON dentro de la carpeta de estado (por defecto ~/.praxis). Los tokens nunca se
guardan en claro, solo su hash SHA-256: el token en claro lo tiene únicamente la PWA.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_TOKENS = 8


@dataclass(frozen=True)
class TokenInfo:
    hash: str
    origin: str  # origen desde el que se emparejó ("" si fue sin cabecera Origin)
    created: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict = {"version": 1, "root": None, "workspace": None, "tokens": []}
        self._load()

    # ---------- disco ----------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return
        if isinstance(data, dict) and data.get("version") == 1:
            self._data.update({k: data[k] for k in ("root", "workspace", "tokens") if k in data})

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)  # en Windows solo cambia el atributo de solo lectura
        os.replace(tmp, self.path)

    # ---------- raíz y carpeta de trabajo ----------
    @property
    def root(self) -> Path | None:
        with self._lock:
            return Path(self._data["root"]) if self._data["root"] else None

    def set_root(self, root: Path) -> None:
        with self._lock:
            if self._data["root"] != str(root):
                self._data["workspace"] = None  # la carpeta de trabajo era relativa a la raíz anterior
            self._data["root"] = str(root)
            self._save()

    @property
    def workspace(self) -> str | None:
        with self._lock:
            return self._data["workspace"]

    def set_workspace(self, rel: str | None) -> None:
        with self._lock:
            self._data["workspace"] = rel
            self._save()

    # ---------- tokens ----------
    def issue_token(self, origin: str) -> str:
        token = secrets.token_urlsafe(32)  # 32 bytes = 256 bits
        entry = {"hash": _hash(token), "origin": origin, "created": datetime.now(UTC).isoformat(timespec="seconds")}
        with self._lock:
            self._data["tokens"] = [*self._data["tokens"], entry][-MAX_TOKENS:]
            self._save()
        return token

    def find_token(self, token: str) -> TokenInfo | None:
        """Busca el token comparando todos los hashes (tiempo constante por comparación)."""
        digest = _hash(token)
        found = None
        with self._lock:
            for entry in self._data["tokens"]:
                if hmac.compare_digest(entry["hash"], digest):
                    found = TokenInfo(entry["hash"], entry.get("origin", ""), entry.get("created", ""))
        return found

    def revoke_token(self, token: str) -> bool:
        digest = _hash(token)
        with self._lock:
            kept = [e for e in self._data["tokens"] if not hmac.compare_digest(e["hash"], digest)]
            changed = len(kept) != len(self._data["tokens"])
            self._data["tokens"] = kept
            if changed:
                self._save()
        return changed

    def revoke_all(self) -> int:
        with self._lock:
            n = len(self._data["tokens"])
            self._data["tokens"] = []
            self._save()
        return n

    def token_count(self) -> int:
        with self._lock:
            return len(self._data["tokens"])
