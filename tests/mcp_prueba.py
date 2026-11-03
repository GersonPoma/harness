"""El servidor MCP falso (tests/datos/servidor_mcp_falso.py) por stdio o por HTTP local."""

import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn

from harness.config_mcp import ServidorHTTP, ServidorStdio
from tests.datos.servidor_mcp_falso import crear_servidor

RUTA_SERVIDOR = Path(__file__).parent / "datos" / "servidor_mcp_falso.py"
HOST = "127.0.0.1"
TIMEOUT_ARRANQUE_SEGUNDOS = 10


def servidor_stdio(env: dict[str, str] | None = None, nombre: str = "github") -> ServidorStdio:
    """El servidor falso como proceso hijo, con el mismo Python de los tests."""
    return ServidorStdio(nombre, sys.executable, (str(RUTA_SERVIDOR),), env or {})


@dataclass
class ServidorHTTPLocal:
    """Servidor falso corriendo en un puerto local, y los headers que recibió en cada pedido."""

    url: str
    headers_recibidos: list[dict[str, str]] = field(default_factory=list)

    def config(self, headers: dict[str, str] | None = None) -> ServidorHTTP:
        return ServidorHTTP("github", self.url, headers or {})


@contextmanager
def levantar_servidor_http() -> Iterator[ServidorHTTPLocal]:
    """Levanta el servidor falso con uvicorn en un puerto libre y lo detiene al salir."""
    local = ServidorHTTPLocal("")
    app = _registrar_headers(crear_servidor().streamable_http_app(), local.headers_recibidos)
    # Puerto 0: el sistema operativo elige uno libre.
    servidor = uvicorn.Server(uvicorn.Config(app, host=HOST, port=0, log_level="warning"))
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    limite = time.monotonic() + TIMEOUT_ARRANQUE_SEGUNDOS
    while not servidor.started:
        if time.monotonic() > limite or not hilo.is_alive():
            raise RuntimeError("El servidor MCP falso no arrancó.")
        time.sleep(0.05)
    puerto = servidor.servers[0].sockets[0].getsockname()[1]
    local.url = f"http://{HOST}:{puerto}/mcp"
    try:
        yield local
    finally:
        servidor.should_exit = True
        hilo.join(TIMEOUT_ARRANQUE_SEGUNDOS)


def _registrar_headers(app: Any, recibidos: list[dict[str, str]]) -> Any:
    async def envoltorio(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            recibidos.append({k.decode(): v.decode() for k, v in scope["headers"]})
        await app(scope, receive, send)

    return envoltorio
