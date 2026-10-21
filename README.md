# praxis-runner

Runner local de [Praxis](https://praxis.gonzaloenbolsa2021.workers.dev), una app de aprendizaje de Python. Es un
programa pequeño que dejas abierto en una terminal mientras estudias: deja que la web vea qué tienes instalado,
compruebe los pasos de los talleres y pase los tests de tus proyectos, **solo en tu ordenador** (`127.0.0.1:47821`).

Sin él, Praxis funciona igual: en cada paso puedes ejecutar el comando tú y pegar su salida (plan B).

Licencia MIT. El código vive en el repositorio de Praxis (carpeta `runner/`) y se publica aquí, tal cual, en cada
versión etiquetada.

## Instalar (Windows, PowerShell)

1. **uv** (si `uv --version` no responde):

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Cierra PowerShell y ábrelo otra vez.

2. **El runner**, siempre de una versión etiquetada (no de la punta de `main`):

   ```powershell
   uv tool install --python 3.14 "git+https://github.com/graamores/praxis-runner@v0.3.0"
   ```

   No hace falta cuenta de GitHub: el repositorio es público. Si sale `warning: … is not on your PATH`, ejecuta
   `uv tool update-shell` y abre PowerShell de nuevo. Comprueba: `praxis-runner --version` → `praxis-runner 0.3.0`.

3. **Arrancar**: `praxis-runner`. La primera vez pregunta tu carpeta de proyectos (Enter para `C:\Users\TU-USUARIO\Praxis`)
   y enseña un **código de emparejamiento** de 8 dígitos (un solo uso, 10 minutos).

4. **Emparejar**: en Praxis (Chrome o Edge, mismo ordenador), *Misiones → Taller*, escribe el código y pulsa
   **Emparejar**. Si Chrome pregunta por el acceso a la red local, **Permitir** (solo habla con `127.0.0.1`).

## Uso

```powershell
praxis-runner                   # arranca y enseña el código de emparejamiento
praxis-runner root [RUTA]       # muestra o cambia la carpeta raíz de trabajo
praxis-runner status            # raíz, carpeta de trabajo y navegadores emparejados
praxis-runner unpair            # olvida todos los navegadores emparejados
praxis-runner --port 50000      # otro puerto (la app usa el 47821)
```

| Variable | Para qué | Por defecto |
|---|---|---|
| `PRAXIS_RUNNER_PORT` | Puerto en 127.0.0.1 | `47821` |
| `PRAXIS_RUNNER_ORIGINS` | Orígenes extra que pueden llamar (comas; nunca `*`) | (ninguno) |
| `PRAXIS_RUNNER_HOME` | Carpeta del estado (`runner_state.json`) | `~/.praxis` |

## Actualizar y desinstalar

```powershell
uv tool install --reinstall --python 3.14 "git+https://github.com/graamores/praxis-runner@v0.3.0"   # otra versión: cambia la etiqueta
praxis-runner unpair; uv tool uninstall praxis-runner                                                # quitarlo
```

Cierra el runner (`Ctrl + C`) antes de actualizar: Windows no deja sustituir un programa en marcha. Desinstalar no borra
tus proyectos; el estado del runner está en `%USERPROFILE%\.praxis` (bórralo si quieres dejarlo todo limpio).

## Qué puede y qué no

**Puede**: escuchar solo en `127.0.0.1:47821`; atender solo a la web de Praxis
(`https://praxis.gonzaloenbolsa2021.workers.dev`, `https://praxis-dev.gonzaloenbolsa2021.workers.dev`) y a
`http://localhost:*` / `http://127.0.0.1:*` para desarrollo, y siempre con el token del emparejamiento; ver las versiones
de Python, uv, Git, VS Code y GitHub CLI; crear carpetas vacías **dentro de tu raíz**; comprobar cosas de una **lista
cerrada** (si existe un fichero, si contiene un texto, el estado de tu repositorio git, unos pocos comandos fijos); y pasar
`pytest` en tu carpeta de trabajo con tiempo máximo (120 s, 300 como mucho) y salida acotada (64 KB).

**No puede**: ejecutar comandos que mande la web ni usar un shell; leer, listar ni escribir fuera de tu raíz (ni dentro:
solo crea carpetas vacías y responde sí/no); cambiar la raíz desde la web; instalar nada ni pedir permisos de administrador.

**No es un sandbox**: los tests de tu proyecto corren con tus permisos. No metas en la carpeta raíz código que no sea tuyo
o de confianza.

## Seguridad

| Amenaza | Defensa |
|---|---|
| Otra web llama al runner (CSRF) | `Origin` en lista cerrada; `POST` solo `application/json` (obliga al *preflight*); sin token solo responde `/health` |
| *DNS rebinding* | `Host` debe ser `127.0.0.1:PUERTO` o `localhost:PUERTO` |
| Fuerza bruta del emparejamiento | Código de 8 dígitos, un uso, 10 min, se anula tras 5 fallos; token de 256 bits guardado solo como hash |
| Comandos arbitrarios | No existen: tipos de comprobación conocidos (pydantic `extra="forbid"`) y comandos por identificador, sin shell |
| Salir de la carpeta | Raíz fijada en la consola; rutas relativas resueltas contra la raíz real (enlaces y uniones incluidos) |
| Agotar recursos o ReDoS | Tiempos y salida acotados, una ejecución a la vez, árbol de procesos matado entero; regex cortas sin retroceso anidado |
| Repo malicioso | git con `core.fsmonitor=false` y sin preguntas; `pytest` sí ejecuta el código del repo (ver arriba) |
| Dependencias | Versiones **exactas** de todo lo que se ejecuta (iguales a `uv.lock`, publicadas hace ≥ 14 días) y versión etiquetada |

¿Has encontrado un fallo de seguridad? Escribe a Gonzalo directamente (no abras una incidencia pública).

## Desarrollo

```bash
uv run pytest            # tests; los de las reglas en JavaScript solo corren dentro del repositorio de Praxis
uv run praxis-runner     # arranca desde el código
```

| Módulo | Para qué |
|---|---|
| `app.py` | Rutas de la API v1 y errores con una sola forma |
| `guard.py` | Middleware de Host, Origin, CORS, Private Network Access y límites de cuerpo |
| `pairing.py`, `state.py` | Código de un solo uso, tokens (solo su hash) y estado persistente |
| `workspace.py` | Raíz y rutas acotadas |
| `checks.py`, `salida.py` | Lista blanca de comprobaciones y reglas de la salida (iguales a las del cliente web) |
| `proc.py`, `ejecucion_tests.py`, `herramientas.py` | Procesos con tiempo y salida acotados, `pytest`, detección de herramientas |
| `cli.py` | Órdenes de consola |

API v1: `GET /health`, `POST /pair`, `POST /unpair`, `GET /version`, `GET /hello`, `GET|POST /workspace`, `POST /check`,
`POST /test`. Errores con la forma `{"error": {"code", "message"}}`.
