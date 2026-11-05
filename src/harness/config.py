"""Configuración del modelo, leída del .env del repo del harness (hasta harness config)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from harness.proveedores.presets import PRESETS, PROVEEDOR_PERSONALIZADO

# El .env vive en la raíz del repo del harness, no en la carpeta desde donde se ejecuta.
RUTA_ENV = Path(__file__).resolve().parents[2] / ".env"

VAR_PROVEEDOR = "HARNESS_PROVEEDOR"
VAR_MODELO = "HARNESS_MODELO"
VAR_BASE_URL = "HARNESS_BASE_URL"
VAR_API_KEY_ENV = "HARNESS_API_KEY_ENV"
# Solo para el pull request: el push usa las credenciales de Git del usuario.
VAR_TOKEN_GITHUB = "GITHUB_TOKEN"
# Servidores MCP del usuario (formato mcpServers), junto al .env mientras se desarrolla.
RUTA_MCP = RUTA_ENV.parent / "mcp.json"
# "0" para no darle al modelo las herramientas de GitHub por MCP (el PR sigue por MCP).
VAR_HERRAMIENTAS_GITHUB_MODELO = "HARNESS_MCP_HERRAMIENTAS_MODELO"
VALORES_DESACTIVADO = ("0", "false", "no")
# Jira Cloud: sitio, usuario y API token (clásico) para la API REST.
VAR_JIRA_URL = "JIRA_URL"
VAR_JIRA_EMAIL = "JIRA_EMAIL"
VAR_JIRA_TOKEN = "JIRA_API_TOKEN"
VARIABLES_JIRA = (VAR_JIRA_URL, VAR_JIRA_EMAIL, VAR_JIRA_TOKEN)


class ErrorConfiguracion(Exception):
    """Falta un valor de configuración o es inválido."""


@dataclass(frozen=True)
class ConfigModelo:
    """Proveedor, modelo y credencial con los que se habla con la IA."""

    proveedor: str
    modelo: str
    base_url: str | None
    # repr=False para que la key no aparezca si alguien imprime o loguea la config.
    api_key: str = field(repr=False)


@dataclass(frozen=True)
class ConfigJira:
    """Sitio de Jira Cloud y credenciales para su API REST."""

    url: str
    email: str = field(repr=False)
    # repr=False para que el token no aparezca si alguien imprime o loguea la config.
    token: str = field(repr=False)


def cargar_env(ruta_env: Path = RUTA_ENV) -> None:
    """Carga el .env en el entorno (también lo usan las referencias ${NOMBRE} de harness.yaml)."""
    load_dotenv(ruta_env)


def cargar_config(ruta_env: Path = RUTA_ENV) -> ConfigModelo:
    """Lee la configuración del .env y valida que esté completa."""
    cargar_env(ruta_env)
    proveedor = _leer_obligatoria(VAR_PROVEEDOR, "el proveedor del modelo").lower()
    if proveedor not in PRESETS:
        disponibles = ", ".join(PRESETS)
        raise ErrorConfiguracion(
            f"Proveedor '{proveedor}' desconocido en {VAR_PROVEEDOR}. Opciones: {disponibles}."
        )
    modelo = _leer_obligatoria(VAR_MODELO, "el id del modelo")

    preset = PRESETS[proveedor]
    base_url = preset.base_url
    variable_api_key = preset.variable_api_key
    if proveedor == PROVEEDOR_PERSONALIZADO:
        base_url = _leer_obligatoria(VAR_BASE_URL, "la URL base del proveedor personalizado")
        variable_api_key = _leer_obligatoria(
            VAR_API_KEY_ENV, "el nombre de la variable con la API key"
        )

    api_key = _leer_obligatoria(variable_api_key, f"la API key del proveedor '{proveedor}'")
    return ConfigModelo(proveedor, modelo, base_url, api_key)


def leer_token_github() -> str | None:
    """Token de GitHub del .env (ya cargado) para crear el PR; None si no está."""
    return os.environ.get(VAR_TOKEN_GITHUB, "").strip() or None


def leer_config_jira() -> ConfigJira:
    """Sitio y credenciales de Jira del .env; lanza ErrorConfiguracion diciendo qué falta."""
    valores = {variable: os.environ.get(variable, "").strip() for variable in VARIABLES_JIRA}
    faltantes = [variable for variable, valor in valores.items() if not valor]
    if faltantes:
        raise ErrorConfiguracion(
            f"Para leer tickets de Jira falta {', '.join(faltantes)} en el .env. Agrégalo en "
            f"{RUTA_ENV} (el API token se crea en https://id.atlassian.com/manage-profile/"
            "security/api-tokens)."
        )
    return ConfigJira(
        valores[VAR_JIRA_URL].rstrip("/"), valores[VAR_JIRA_EMAIL], valores[VAR_JIRA_TOKEN]
    )


def leer_herramientas_github_modelo() -> bool:
    """Si el modelo recibe las herramientas de GitHub por MCP (por defecto, sí)."""
    valor = os.environ.get(VAR_HERRAMIENTAS_GITHUB_MODELO, "").strip().lower()
    return valor not in VALORES_DESACTIVADO


def _leer_obligatoria(variable: str, que_es: str) -> str:
    valor = os.environ.get(variable, "").strip()
    if not valor:
        raise ErrorConfiguracion(
            f"Falta {variable} en el .env ({que_es}). Agrégala en {RUTA_ENV}."
        )
    return valor
