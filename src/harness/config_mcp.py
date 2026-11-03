"""Servidores MCP del usuario, en mcp.json con el formato "mcpServers" de otros clientes MCP.

Es configuración de cada máquina (credenciales, Docker), así que nunca va en harness.yaml. En
desarrollo vive en la raíz del repo del harness, junto al .env; los secretos no se escriben
ahí: se referencian como ${NOMBRE} y se toman del entorno.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.variables import resolver_mapa

CLAVE_SERVIDORES = "mcpServers"
TIPO_STDIO = "stdio"
TIPO_HTTP = "http"
# El servidor que cumple el rol de GitHub (herramientas de lectura y el PR).
SERVIDOR_GITHUB = "github"
# Para usar otro servidor de mcp.json como GitHub (por ejemplo el remoto) sin editar el JSON.
VAR_SERVIDOR_GITHUB = "HARNESS_MCP_GITHUB"


class ErrorConfigMCP(Exception):
    """mcp.json no se puede leer o tiene un servidor mal configurado."""


@dataclass(frozen=True)
class ServidorStdio:
    """Servidor que el harness lanza como proceso hijo y con el que habla por stdin/stdout."""

    nombre: str
    comando: str
    args: tuple[str, ...]
    # repr=False: con los ${...} ya expandidos puede tener tokens.
    env: dict[str, str] = field(default_factory=dict, repr=False)
    # Valores que salieron de ${...}: se ocultan en el log, la consola y las salidas.
    secretos: tuple[str, ...] = field(default=(), repr=False)

    @property
    def transporte(self) -> str:
        """Nombre del transporte, para los avisos de consola."""
        return TIPO_STDIO


@dataclass(frozen=True)
class ServidorHTTP:
    """Servidor que ya está corriendo (local o remoto), por Streamable HTTP."""

    nombre: str
    url: str = field(repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    secretos: tuple[str, ...] = field(default=(), repr=False)

    @property
    def transporte(self) -> str:
        """Nombre del transporte, para los avisos de consola."""
        return TIPO_HTTP


ServidorMCP = ServidorStdio | ServidorHTTP


def cargar_config_mcp(
    ruta: Path, entorno: Mapping[str, str] = os.environ
) -> dict[str, ServidorMCP]:
    """Lee mcp.json y expande los ${NOMBRE}; sin archivo no hay servidores (no es un error)."""
    if not ruta.is_file():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ErrorConfigMCP(f"No se pudo leer {ruta.name}: {error}") from error
    servidores = datos.get(CLAVE_SERVIDORES) if isinstance(datos, dict) else None
    if not isinstance(servidores, dict):
        raise ErrorConfigMCP(f"{ruta.name} debe tener un objeto '{CLAVE_SERVIDORES}'.")
    return {
        nombre: _leer_servidor(nombre, valores, entorno)
        for nombre, valores in servidores.items()
    }


def elegir_servidor_github(
    servidores: Mapping[str, ServidorMCP], entorno: Mapping[str, str] = os.environ
) -> ServidorMCP | None:
    """El servidor de GitHub: el de HARNESS_MCP_GITHUB, o el llamado 'github'; None si no hay."""
    elegido = entorno.get(VAR_SERVIDOR_GITHUB, "").strip()
    if not elegido:
        return servidores.get(SERVIDOR_GITHUB)
    if elegido not in servidores:
        disponibles = ", ".join(servidores) or "ninguno"
        raise ErrorConfigMCP(
            f"{VAR_SERVIDOR_GITHUB}={elegido}, pero mcp.json no tiene ese servidor "
            f"(hay: {disponibles})."
        )
    return servidores[elegido]


def _leer_servidor(nombre: str, valores: Any, entorno: Mapping[str, str]) -> ServidorMCP:
    if not isinstance(valores, dict):
        raise ErrorConfigMCP(f"El servidor MCP '{nombre}' debe ser un objeto.")
    tipo = valores.get("type") or (TIPO_STDIO if "command" in valores else None)
    if tipo == TIPO_STDIO:
        return _leer_stdio(nombre, valores, entorno)
    if tipo == TIPO_HTTP:
        return _leer_http(nombre, valores, entorno)
    raise ErrorConfigMCP(
        f"El servidor MCP '{nombre}' necesita 'command' (stdio) o \"type\": \"http\" con 'url'."
    )


def _leer_stdio(nombre: str, valores: dict, entorno: Mapping[str, str]) -> ServidorStdio:
    comando = valores.get("command")
    if not isinstance(comando, str) or not comando.strip():
        raise ErrorConfigMCP(f"El servidor MCP '{nombre}' necesita 'command'.")
    args = _lista_de_textos(nombre, "args", valores.get("args", []))
    secretos: list[str] = []
    env = _expandir(nombre, "env", _mapa_de_textos(nombre, "env", valores), entorno, secretos)
    return ServidorStdio(nombre, comando, tuple(args), env, tuple(secretos))


def _leer_http(nombre: str, valores: dict, entorno: Mapping[str, str]) -> ServidorHTTP:
    url = valores.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ErrorConfigMCP(f"El servidor MCP '{nombre}' (http) necesita 'url'.")
    secretos: list[str] = []
    url = _expandir(nombre, "url", {"url": url}, entorno, secretos)["url"]
    headers = _expandir(
        nombre, "headers", _mapa_de_textos(nombre, "headers", valores), entorno, secretos
    )
    return ServidorHTTP(nombre, url, headers, tuple(secretos))


def _expandir(
    nombre: str,
    clave: str,
    valores: dict[str, str],
    entorno: Mapping[str, str],
    secretos: list[str],
) -> dict[str, str]:
    faltantes: list[str] = []
    resultado = resolver_mapa(
        valores, f"mcpServers.{nombre}.{clave}", entorno, secretos, faltantes
    )
    if faltantes:
        raise ErrorConfigMCP("mcp.json usa variables de entorno que no existen: " + "; ".join(
            faltantes
        ))
    return resultado


def _mapa_de_textos(nombre: str, clave: str, valores: dict) -> dict[str, str]:
    mapa = valores.get(clave, {})
    if not isinstance(mapa, dict) or not all(isinstance(v, str) for v in mapa.values()):
        raise ErrorConfigMCP(
            f"'{clave}' del servidor MCP '{nombre}' debe ser un objeto de textos."
        )
    return mapa


def _lista_de_textos(nombre: str, clave: str, valor: Any) -> list[str]:
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise ErrorConfigMCP(f"'{clave}' del servidor MCP '{nombre}' debe ser una lista de textos.")
    return valor
