"""Etiquetas de Docker del harness: qué recurso es suyo, de qué ejecución, y si sigue viva.

Todo lo que el harness crea en Docker (contenedores, redes, volúmenes, servidores MCP) lleva
la etiqueta harness=1. Para limpiar, se lista siempre por etiqueta, nunca por nombre: así
nunca se toca algo que no creó el harness. Cada ejecución anota además el proceso que la
corre (pid y host): un recurso cuyo proceso ya terminó es un resto huérfano.
"""

import json
import os
import socket
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

ETIQUETA_HARNESS = "harness"
VALOR_HARNESS = "1"
ETIQUETA_PROYECTO = "harness.proyecto"
ETIQUETA_TICKET = "harness.ticket"
ETIQUETA_EJECUCION = "harness.ejecucion"
ETIQUETA_PID = "harness.pid"
ETIQUETA_HOST = "harness.host"
# Recursos que el usuario pidió conservar (--conservar) para depurar.
ETIQUETA_CONSERVAR = "harness.conservar"
ETIQUETA_TIPO = "harness.tipo"
TIPO_MCP = "mcp"
FILTRO_HARNESS = f"label={ETIQUETA_HARNESS}={VALOR_HARNESS}"
TIMEOUT_LISTAR_SEGUNDOS = 60

# Tipos de recurso, como los nombra el CLI de docker.
CONTENEDOR = "contenedor"
RED = "red"
VOLUMEN = "volumen"

# Windows: consultar un proceso sin permisos especiales, y el código de "sigue corriendo".
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5

Ejecutor = Callable[[list[str], float], tuple[int, str]]


@dataclass(frozen=True)
class Recurso:
    """Un contenedor, red o volumen de Docker, con sus etiquetas."""

    tipo: str
    id: str
    nombre: str
    etiquetas: dict[str, str] = field(default_factory=dict)
    es_corriendo: bool = False
    # Volúmenes que monta el contenedor (para no borrar uno en uso).
    volumenes: tuple[str, ...] = ()
    # Lo que ocupa en disco (la capa escribible de un contenedor), si se pidió y se sabe.
    bytes: int | None = None

    @property
    def es_conservado(self) -> bool:
        return self.etiquetas.get(ETIQUETA_CONSERVAR) == VALOR_HARNESS


def host_actual() -> str:
    """El nombre de esta máquina, para distinguir recursos de otra (un Docker remoto)."""
    return socket.gethostname()


def etiquetas_del_proceso() -> dict[str, str]:
    """harness=1 y el proceso que crea el recurso: con eso se sabe si sigue vivo."""
    return {
        ETIQUETA_HARNESS: VALOR_HARNESS,
        ETIQUETA_PID: str(os.getpid()),
        ETIQUETA_HOST: host_actual(),
    }


def etiquetas_ejecucion(
    proyecto: str, ticket: str, ejecucion: str, es_conservar: bool = False
) -> dict[str, str]:
    """Las etiquetas de los contenedores y la red de una ejecución."""
    etiquetas = {
        **etiquetas_del_proceso(),
        ETIQUETA_PROYECTO: proyecto,
        ETIQUETA_TICKET: ticket,
        ETIQUETA_EJECUCION: ejecucion,
    }
    if es_conservar:
        etiquetas[ETIQUETA_CONSERVAR] = VALOR_HARNESS
    return etiquetas


def argumentos_etiquetas(etiquetas: Mapping[str, str]) -> list[str]:
    """['--label', 'clave=valor', ...] para docker run, network create o volume create."""
    argumentos: list[str] = []
    for clave, valor in etiquetas.items():
        argumentos += ["--label", f"{clave}={valor}"]
    return argumentos


def proceso_vivo(pid: int) -> bool:
    """True si el proceso existe en esta máquina. Ante la duda, True (no se borra nada)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _proceso_vivo_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Existe, pero es de otro usuario.
    except OSError:
        return True
    return True


def _proceso_vivo_windows(pid: int) -> bool:
    # En Windows os.kill(pid, 0) no pregunta: termina el proceso. Por eso se usa la API.
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    manejador = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not manejador:
        # Sin permiso para abrirlo: existe. Cualquier otro error: no existe.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        codigo = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(manejador, ctypes.byref(codigo)):
            return True
        return codigo.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(manejador)


def es_de_ejecucion_viva(
    etiquetas: Mapping[str, str],
    host: str | None = None,
    vivo: Callable[[int], bool] | None = None,
) -> bool:
    """True si el recurso pertenece a una ejecución que sigue corriendo (o no se puede saber).

    - De otra máquina (un Docker compartido): siempre vivo, nunca se toca desde aquí.
    - Sin pid: lo creó una versión anterior del harness, que no lo anotaba; no está vivo.
    - Con pid de esta máquina: vivo si el proceso existe.
    """
    host_recurso = etiquetas.get(ETIQUETA_HOST)
    if host_recurso and host_recurso != (host or host_actual()):
        return True
    pid = etiquetas.get(ETIQUETA_PID, "")
    if not pid.isdigit():
        return False
    return (vivo or proceso_vivo)(int(pid))


def listar(
    ejecutar: Ejecutor, tipo: str, filtros: list[str], con_tamano: bool = False
) -> list[Recurso]:
    """Los recursos de un tipo que cumplen todos los filtros ('label=...'), con sus datos.

    Con `con_tamano`, los contenedores traen lo que ocupan (docker inspect --size). Si docker
    falla o responde algo que no se entiende, la lista sale vacía: quien la usa para borrar
    no borra nada.
    """
    argumentos_filtro = [parte for filtro in filtros for parte in ("--filter", filtro)]
    listado = {
        CONTENEDOR: ["docker", "ps", "-a", "-q", "--no-trunc"],
        RED: ["docker", "network", "ls", "-q", "--no-trunc"],
        VOLUMEN: ["docker", "volume", "ls", "-q"],
    }[tipo]
    codigo, salida = ejecutar([*listado, *argumentos_filtro], TIMEOUT_LISTAR_SEGUNDOS)
    ids = salida.split() if codigo == 0 else []
    if not ids:
        return []
    inspeccion = {
        CONTENEDOR: ["docker", "inspect", *(["--size"] if con_tamano else [])],
        RED: ["docker", "network", "inspect"],
        VOLUMEN: ["docker", "volume", "inspect"],
    }[tipo]
    codigo, salida = ejecutar([*inspeccion, *ids], TIMEOUT_LISTAR_SEGUNDOS)
    if codigo != 0:
        return []
    try:
        datos = json.loads(salida)
    except json.JSONDecodeError:
        return []
    if not isinstance(datos, list):
        return []
    return [r for r in (_a_recurso(tipo, d) for d in datos if isinstance(d, dict)) if r]


def _a_recurso(tipo: str, datos: dict) -> Recurso | None:
    if tipo == CONTENEDOR:
        config = datos.get("Config") or {}
        estado = datos.get("State") or {}
        montajes = datos.get("Mounts") or []
        tamano = datos.get("SizeRw")
        return Recurso(
            tipo,
            str(datos.get("Id", "")),
            str(datos.get("Name", "")).lstrip("/"),
            dict(config.get("Labels") or {}),
            bool(estado.get("Running")),
            tuple(str(m.get("Name")) for m in montajes if m.get("Type") == "volume"),
            tamano if isinstance(tamano, int) else None,
        )
    nombre = str(datos.get("Name", ""))
    # Los volúmenes no tienen id: se identifican (y se borran) por su nombre.
    identificador = str(datos.get("Id", "")) if tipo == RED else nombre
    if not identificador:
        return None
    return Recurso(tipo, identificador, nombre, dict(datos.get("Labels") or {}))
