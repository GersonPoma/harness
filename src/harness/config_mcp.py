"""Servidores MCP del usuario, en mcp.json con el formato "mcpServers" de otros clientes MCP.

Es configuración de cada máquina (credenciales, Docker), así que nunca va en harness.yaml. En
desarrollo vive en la raíz del repo del harness, junto al .env; los secretos no se escriben
ahí: se referencian como ${NOMBRE} y se toman del entorno.
"""

import base64
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
# El de Jira (comentar el link del PR y leer los comentarios), y su variable.
SERVIDOR_JIRA = "jira"
VAR_SERVIDOR_JIRA = "HARNESS_MCP_JIRA"
# HARNESS_MCP_GITHUB=rest o HARNESS_MCP_JIRA=rest: la API REST aunque haya servidor en mcp.json
# (por ejemplo, para mostrar los dos caminos sin editar el JSON). Por eso es un nombre reservado.
VALOR_SOLO_REST = "rest"
# Autenticación Basic explícita en servidores HTTP: el harness arma el header con base64.
CLAVE_AUTH = "auth"
CLAVE_BASIC = "basic"
CAMPOS_BASIC = ("usuario", "token")
HEADER_AUTORIZACION = "Authorization"


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
    # Las variables de entorno de donde salieron (para avisar si alguna es demasiado corta).
    variables_secretas: tuple[str, ...] = ()

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
    variables_secretas: tuple[str, ...] = ()

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
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ErrorConfigMCP(f"No se pudo leer {ruta.name}: {error}") from error
    servidores = datos.get(CLAVE_SERVIDORES) if isinstance(datos, dict) else None
    if not isinstance(servidores, dict):
        raise ErrorConfigMCP(f"{ruta.name} debe tener un objeto '{CLAVE_SERVIDORES}'.")
    reservados = [nombre for nombre in servidores if nombre.strip().lower() == VALOR_SOLO_REST]
    if reservados:
        raise ErrorConfigMCP(
            f"'{reservados[0]}' es un nombre reservado en {ruta.name}: {VAR_SERVIDOR_GITHUB}=rest "
            f"y {VAR_SERVIDOR_JIRA}=rest fuerzan la API REST. Usa otro nombre para el servidor."
        )
    return {
        nombre: _leer_servidor(nombre, valores, entorno)
        for nombre, valores in servidores.items()
    }


def elegir_servidor_github(
    servidores: Mapping[str, ServidorMCP], entorno: Mapping[str, str] = os.environ
) -> ServidorMCP | None:
    """El servidor de GitHub: el de HARNESS_MCP_GITHUB, o el llamado 'github'; None si no hay."""
    return elegir_servidor(servidores, SERVIDOR_GITHUB, VAR_SERVIDOR_GITHUB, entorno)


def elegir_servidor_jira(
    servidores: Mapping[str, ServidorMCP], entorno: Mapping[str, str] = os.environ
) -> ServidorMCP | None:
    """El servidor de Jira: el de HARNESS_MCP_JIRA, o el llamado 'jira'; None si no hay."""
    return elegir_servidor(servidores, SERVIDOR_JIRA, VAR_SERVIDOR_JIRA, entorno)


def elegir_servidor(
    servidores: Mapping[str, ServidorMCP],
    nombre: str,
    variable: str,
    entorno: Mapping[str, str] = os.environ,
) -> ServidorMCP | None:
    """El servidor que nombra la variable o, si no está definida, el que se llama `nombre`.

    Con la variable en "rest", ninguno: se usa la API REST.
    """
    elegido = entorno.get(variable, "").strip()
    if es_solo_rest(variable, entorno):
        return None
    if not elegido:
        return servidores.get(nombre)
    if elegido not in servidores:
        disponibles = ", ".join(servidores) or "ninguno"
        raise ErrorConfigMCP(
            f"{variable}={elegido}, pero mcp.json no tiene ese servidor (hay: {disponibles})."
        )
    return servidores[elegido]


def es_solo_rest(variable: str, entorno: Mapping[str, str] = os.environ) -> bool:
    """True si la variable (HARNESS_MCP_GITHUB o HARNESS_MCP_JIRA) fuerza la API REST."""
    return entorno.get(variable, "").strip().lower() == VALOR_SOLO_REST


def motivo_sin_servidor(
    variable: str, servicio: str, entorno: Mapping[str, str] = os.environ
) -> str:
    """Por qué no hay servidor MCP: se forzó la API REST, o mcp.json no lo tiene."""
    if es_solo_rest(variable, entorno):
        return f"{variable}={VALOR_SOLO_REST}"
    return f"no hay servidor MCP de {servicio} en mcp.json"


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
    variables: list[str] = []
    env = _expandir(
        nombre, "env", _mapa_de_textos(nombre, "env", valores), entorno, secretos, variables
    )
    return ServidorStdio(nombre, comando, tuple(args), env, tuple(secretos), tuple(variables))


def _leer_http(nombre: str, valores: dict, entorno: Mapping[str, str]) -> ServidorHTTP:
    url = valores.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ErrorConfigMCP(f"El servidor MCP '{nombre}' (http) necesita 'url'.")
    secretos: list[str] = []
    variables: list[str] = []
    url = _expandir(nombre, "url", {"url": url}, entorno, secretos, variables)["url"]
    headers = _expandir(
        nombre, "headers", _mapa_de_textos(nombre, "headers", valores), entorno, secretos,
        variables,
    )
    if CLAVE_AUTH in valores:
        headers[HEADER_AUTORIZACION] = _header_basic(
            nombre, valores, headers, entorno, secretos, variables
        )
    return ServidorHTTP(nombre, url, headers, tuple(secretos), tuple(variables))


def _header_basic(
    nombre: str,
    valores: dict,
    headers: dict[str, str],
    entorno: Mapping[str, str],
    secretos: list[str],
    variables: list[str],
) -> str:
    """"auth": {"basic": {"usuario": ..., "token": ...}} -> "Basic base64(usuario:token)".

    Así el token se escribe una sola vez (como ${...}) y no hay que recalcular nada si cambia.
    """
    auth = valores[CLAVE_AUTH]
    basic = auth.get(CLAVE_BASIC) if isinstance(auth, dict) and len(auth) == 1 else None
    if not isinstance(basic, dict) or set(basic) != set(CAMPOS_BASIC):
        raise ErrorConfigMCP(
            f"'auth' del servidor MCP '{nombre}' debe ser "
            '{"basic": {"usuario": "...", "token": "..."}}.'
        )
    if any(clave.lower() == HEADER_AUTORIZACION.lower() for clave in headers):
        raise ErrorConfigMCP(
            f"El servidor MCP '{nombre}' tiene 'auth' y además un header Authorization: "
            "usa solo uno de los dos."
        )
    datos = _expandir(nombre, CLAVE_AUTH, _mapa_de_textos(nombre, CLAVE_BASIC, auth), entorno,
                      secretos, variables)
    credencial = base64.b64encode(f"{datos['usuario']}:{datos['token']}".encode()).decode()
    # El base64 también es secreto: alcanza para entrar, igual que el token.
    secretos.append(credencial)
    return f"Basic {credencial}"


def _expandir(
    nombre: str,
    clave: str,
    valores: dict[str, str],
    entorno: Mapping[str, str],
    secretos: list[str],
    variables: list[str],
) -> dict[str, str]:
    faltantes: list[str] = []
    resultado = resolver_mapa(
        valores, f"mcpServers.{nombre}.{clave}", entorno, secretos, faltantes, variables
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
