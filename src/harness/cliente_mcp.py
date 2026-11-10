"""Sesión con un servidor MCP (stdio o HTTP), con una interfaz síncrona para el harness.

El SDK de MCP es asíncrono y el harness no: un portal de anyio corre el event loop en su propio
hilo. Una sola tarea del portal abre el cliente, espera la orden de cerrar y lo cierra; así sus
grupos de tareas entran y salen en la misma tarea, como exige anyio. Las llamadas a
herramientas se piden desde el hilo principal con portal.call.
"""

import os
import re
import subprocess
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import anyio
import httpx2
from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import Client, MCPError, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import streamable_http_client

from harness.config_mcp import ServidorHTTP, ServidorMCP, ServidorStdio, es_docker_run
from harness.etiquetas import (
    ETIQUETA_TICKET,
    ETIQUETA_TIPO,
    TIPO_MCP,
    argumentos_etiquetas,
    etiquetas_del_proceso,
)
from harness.variables import ocultar_secretos

# Generoso: la primera vez, 'docker run' descarga la imagen del servidor.
TIMEOUT_CONEXION_SEGUNDOS = 180
TIMEOUT_LLAMADA_SEGUNDOS = 60
# El SDK le da al proceso unos 6 s para salir: cerrar stdin, terminarlo y esperarlo.
TIMEOUT_CIERRE_SEGUNDOS = 15
TIMEOUT_DOCKER_RM_SEGUNDOS = 30
# Esperas cortas: un solo wait largo no siempre se corta con Ctrl+C en Windows.
INTERVALO_ESPERA_SEGUNDOS = 0.2
# Los mismos que usa el SDK por defecto para HTTP: lectura larga porque usa SSE.
TIMEOUT_HTTP = httpx2.Timeout(30.0, read=300.0)
# Docker los necesita para encontrar el daemon; get_default_environment no los incluye.
VARIABLES_DOCKER = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH")
# Nombres de contenedor válidos para Docker: [a-zA-Z0-9][a-zA-Z0-9_.-]*
PATRON_NO_PERMITIDO_EN_NOMBRE = re.compile(r"[^a-zA-Z0-9_.-]")
# Si no conecta, la causa real suele estar en lo que el servidor escribió en stderr (por
# ejemplo, "docker: error during connect"): se muestran sus últimas líneas.
MAX_LINEAS_LOG_ERROR = 5


class ErrorMCP(Exception):
    """El servidor MCP no arrancó, no respondió a tiempo o la sesión se cortó."""


@dataclass(frozen=True)
class HerramientaMCP:
    """Una herramienta de tools/list, con tipos propios (nunca los del SDK)."""

    nombre: str
    descripcion: str
    esquema: dict[str, Any]


@dataclass(frozen=True)
class ResultadoMCP:
    """Texto de una llamada a tools/call; es_error si la herramienta falló."""

    texto: str
    es_error: bool


def crear_entorno_hijo(
    env: Mapping[str, str], entorno: Mapping[str, str] = os.environ
) -> dict[str, str]:
    """Entorno del proceso del servidor: lo mínimo del sistema, Docker y el 'env' del servidor.

    Nunca el entorno completo: ahí están las API keys del .env, que el servidor no necesita.
    """
    hijo = get_default_environment()
    hijo |= {nombre: entorno[nombre] for nombre in VARIABLES_DOCKER if entorno.get(nombre)}
    return hijo | dict(env)


def nombre_contenedor(servidor: str, ticket: str) -> str:
    """Nombre único del contenedor del servidor, para poder borrarlo si queda huérfano."""
    nombre = f"harness-mcp-{servidor}-{ticket}-{os.getpid()}"
    return PATRON_NO_PERMITIDO_EN_NOMBRE.sub("-", nombre).lower()


def agregar_nombre_docker(
    comando: str, args: list[str], nombre: str, ticket: str = ""
) -> tuple[list[str], bool]:
    """En 'docker run', agrega las etiquetas del harness y --name si no lo trae.

    Devuelve los args y si agregó el nombre. Las etiquetas permiten que 'harness limpiar'
    encuentre el contenedor si queda huérfano.
    """
    if not es_docker_run(comando, args):
        return args, False
    etiquetas = {**etiquetas_del_proceso(), ETIQUETA_TIPO: TIPO_MCP, ETIQUETA_TICKET: ticket}
    con_etiquetas = ["run", *argumentos_etiquetas(etiquetas), *args[1:]]
    if any(a == "--name" or a.startswith("--name=") for a in args):
        return con_etiquetas, False
    return ["run", "--name", nombre, *con_etiquetas[1:]], True


class SesionMCP:
    """Conexión abierta con un servidor MCP. Usar con 'with': al salir siempre se cierra."""

    def __init__(self, servidor: ServidorMCP, ruta_log: Path, ticket: str) -> None:
        self.servidor = servidor
        self._ruta_log = ruta_log
        self._ticket = ticket
        self.herramientas: list[HerramientaMCP] = []
        self._pila = ExitStack()
        self._portal: BlockingPortal | None = None
        self._tarea: Future[None] | None = None
        self._cliente: Client | None = None
        self._cerrar: anyio.Event | None = None
        self._lista = threading.Event()
        self._contenedor: str | None = None
        # Dónde empieza en el log lo que escribió el servidor en esta sesión.
        self._inicio_log: int | None = None

    def __enter__(self) -> "SesionMCP":
        try:
            self._portal = self._pila.enter_context(start_blocking_portal())
            transporte = self._crear_transporte()
            self._pila.callback(self._detener)
            self._tarea = self._portal.start_task_soon(self._mantener, transporte)
            self._esperar_conexion()
        except BaseException:
            self._pila.close()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self._pila.close()

    def llamar(self, nombre: str, argumentos: dict[str, Any]) -> ResultadoMCP:
        """tools/call; un error de la herramienta vuelve como resultado, uno de la sesión no."""
        if self._portal is None or self._tarea is None or self._tarea.done():
            raise ErrorMCP(f"La sesión con el servidor MCP '{self.servidor.nombre}' se cerró.")
        try:
            return self._portal.call(self._llamar, nombre, argumentos)
        except TimeoutError as error:
            raise ErrorMCP(
                f"'{nombre}' no respondió en {TIMEOUT_LLAMADA_SEGUNDOS} s "
                f"(servidor MCP '{self.servidor.nombre}')."
            ) from error
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, httpx2.HTTPError) as error:
            raise ErrorMCP(
                f"Se cortó la conexión con el servidor MCP '{self.servidor.nombre}': {error}"
            ) from error

    def _crear_transporte(self) -> Any:
        if isinstance(self.servidor, ServidorHTTP):
            http = httpx2.AsyncClient(headers=self.servidor.headers, timeout=TIMEOUT_HTTP)
            return streamable_http_client(self.servidor.url, http_client=http)
        return stdio_client(self._parametros_stdio(self.servidor), errlog=self._abrir_log())

    def _parametros_stdio(self, servidor: ServidorStdio) -> StdioServerParameters:
        args, es_nombrado = agregar_nombre_docker(
            servidor.comando,
            list(servidor.args),
            nombre_contenedor(servidor.nombre, self._ticket),
            self._ticket,
        )
        if es_nombrado:
            self._contenedor = args[2]
            self._pila.callback(self._borrar_contenedor)
        return StdioServerParameters(
            command=servidor.comando, args=args, env=crear_entorno_hijo(servidor.env)
        )

    def _abrir_log(self) -> TextIO:
        # Los logs del servidor van a un archivo: en la consola se mezclarían con el harness.
        self._ruta_log.parent.mkdir(parents=True, exist_ok=True)
        self._inicio_log = self._ruta_log.stat().st_size if self._ruta_log.exists() else 0
        return self._pila.enter_context(self._ruta_log.open("a", encoding="utf-8"))

    async def _mantener(self, transporte: Any) -> None:
        self._cerrar = anyio.Event()
        try:
            async with Client(transporte) as cliente:
                respuesta = await cliente.list_tools()
                self.herramientas = [_a_herramienta(h) for h in respuesta.tools]
                self._cliente = cliente
                self._lista.set()
                await self._cerrar.wait()
        finally:
            # También si falló al conectar: así el hilo principal deja de esperar.
            self._lista.set()

    def _esperar_conexion(self) -> None:
        limite = time.monotonic() + TIMEOUT_CONEXION_SEGUNDOS
        while not self._lista.wait(INTERVALO_ESPERA_SEGUNDOS):
            if time.monotonic() > limite:
                raise ErrorMCP(
                    f"El servidor MCP '{self.servidor.nombre}' no respondió en "
                    f"{TIMEOUT_CONEXION_SEGUNDOS} s.{self._salida_del_servidor()}"
                )
        if self._cliente is None:
            raise ErrorMCP(
                f"No se pudo conectar con el servidor MCP '{self.servidor.nombre}': "
                f"{_describir_fallo(self._tarea)}{self._salida_del_servidor()}"
            )

    def _salida_del_servidor(self) -> str:
        """Las últimas líneas que el servidor escribió en su log en esta sesión, sin secretos."""
        if self._inicio_log is None:
            return ""
        try:
            with self._ruta_log.open("rb") as archivo:
                archivo.seek(self._inicio_log)
                texto = archivo.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        lineas = [linea.rstrip() for linea in texto.splitlines() if linea.strip()]
        if not lineas:
            return ""
        ultimas = ocultar_secretos("\n".join(lineas[-MAX_LINEAS_LOG_ERROR:]), self.servidor.secretos)
        return f"\nLo último que escribió el servidor:\n{ultimas}"

    async def _llamar(self, nombre: str, argumentos: dict[str, Any]) -> ResultadoMCP:
        with anyio.fail_after(TIMEOUT_LLAMADA_SEGUNDOS):
            try:
                resultado = await self._cliente.call_tool(nombre, argumentos)
            except MCPError as error:
                # El servidor rechazó el pedido (herramienta o argumentos): el modelo lo ve.
                return ResultadoMCP(f"Error del servidor MCP: {error}", es_error=True)
        textos = [bloque.text for bloque in resultado.content if bloque.type == "text"]
        return ResultadoMCP("\n".join(textos), bool(resultado.is_error))

    def _detener(self) -> None:
        tarea, portal = self._tarea, self._portal
        if tarea is None or portal is None or tarea.done():
            return
        if self._cliente is None or self._cerrar is None:
            # Todavía conectando (timeout o Ctrl+C): no hay nada que cerrar en orden.
            tarea.cancel()
            return
        portal.call(self._cerrar.set)
        try:
            tarea.result(timeout=TIMEOUT_CIERRE_SEGUNDOS)
        except BaseException:  # noqa: BLE001 - al cerrar, cualquier fallo solo deja de esperar
            tarea.cancel()

    def _borrar_contenedor(self) -> None:
        # Último recurso: si el servidor no salió a tiempo, el SDK mata el cliente 'docker',
        # pero el contenedor puede seguir corriendo.
        try:
            subprocess.run(
                ["docker", "rm", "-f", self._contenedor],
                capture_output=True,
                timeout=TIMEOUT_DOCKER_RM_SEGUNDOS,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def _a_herramienta(herramienta: Any) -> HerramientaMCP:
    return HerramientaMCP(
        herramienta.name, herramienta.description or "", dict(herramienta.input_schema)
    )


def _describir_fallo(tarea: Future[None] | None) -> str:
    if tarea is None or not tarea.done():
        return "se cortó al iniciar."
    if tarea.cancelled():
        return "se canceló."
    error = tarea.exception()
    if error is None:
        return "se cerró al iniciar."
    # anyio envuelve el error real en grupos ("unhandled errors in a TaskGroup"): se muestran
    # las excepciones de adentro, que son las que dicen qué pasó.
    causas = [_describir_excepcion(hoja) for hoja in _hojas(error)]
    return "; ".join(dict.fromkeys(causas))


def _hojas(error: BaseException) -> list[BaseException]:
    """Las excepciones de un grupo (y de sus grupos anidados), o la misma si no es un grupo."""
    if isinstance(error, BaseExceptionGroup):
        return [hoja for interna in error.exceptions for hoja in _hojas(interna)]
    return [error]


def _describir_excepcion(error: BaseException) -> str:
    texto = str(error).strip()
    return f"{type(error).__name__}: {texto}" if texto else type(error).__name__
