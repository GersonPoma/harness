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


def _leer_obligatoria(variable: str, que_es: str) -> str:
    valor = os.environ.get(variable, "").strip()
    if not valor:
        raise ErrorConfiguracion(
            f"Falta {variable} en el .env ({que_es}). Agrégala en {RUTA_ENV}."
        )
    return valor
