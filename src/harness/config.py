"""Configuración del modelo y credenciales: opciones, entorno, .env y ~/.harness/config.toml.

Orden de prioridad del modelo (gana el primero que tenga valor): --proveedor/--modelo,
--perfil, las variables de entorno o el .env (HARNESS_PROVEEDOR...) y el perfil predeterminado
de config.toml. Las credenciales salen del entorno real, después del .env y por último de
config.toml.
"""

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

from harness.config_usuario import (
    ConfigUsuario,
    ErrorConfigUsuario,
    leer_config_usuario,
    ruta_config,
    ruta_mcp_usuario,
)
from harness.proveedores.presets import PRESETS, PROVEEDOR_PERSONALIZADO

# El .env vive en la raíz del repo del harness, no en la carpeta desde donde se ejecuta.
RUTA_ENV = Path(__file__).resolve().parents[2] / ".env"

# Las de configuración (no secretos) empiezan así: no se guardan como credencial.
PREFIJO_VARIABLES_HARNESS = "HARNESS_"
VAR_PROVEEDOR = "HARNESS_PROVEEDOR"
VAR_MODELO = "HARNESS_MODELO"
VAR_BASE_URL = "HARNESS_BASE_URL"
VAR_API_KEY_ENV = "HARNESS_API_KEY_ENV"
# Proveedores de respaldo, en orden: si el principal se cae, la ejecución sigue con estos.
VAR_RESPALDO = "HARNESS_RESPALDO"
SEPARADOR_RESPALDO = ","
SEPARADOR_PROVEEDOR = ":"
ORIGEN_RESPALDO_USUARIO = "respaldo de config.toml"
# Solo para el pull request: el push usa las credenciales de Git del usuario.
VAR_TOKEN_GITHUB = "GITHUB_TOKEN"
# Servidores MCP del usuario (formato mcpServers): ~/.harness/mcp.json; si no existe, el de
# desarrollo, junto al .env.
RUTA_MCP_DESARROLLO = RUTA_ENV.parent / "mcp.json"
# "0" para no darle al modelo las herramientas de GitHub por MCP (el PR sigue por MCP).
VAR_HERRAMIENTAS_GITHUB_MODELO = "HARNESS_MCP_HERRAMIENTAS_MODELO"
VALORES_DESACTIVADO = ("0", "false", "no")
# Jira Cloud: sitio, usuario y API token (clásico) para la API REST.
VAR_JIRA_URL = "JIRA_URL"
VAR_JIRA_EMAIL = "JIRA_EMAIL"
VAR_JIRA_TOKEN = "JIRA_API_TOKEN"
VARIABLES_JIRA = (VAR_JIRA_URL, VAR_JIRA_EMAIL, VAR_JIRA_TOKEN)
# De dónde salió una variable que cargó el harness (para decirlo en los mensajes).
ORIGEN_ENTORNO = "el entorno"
ORIGEN_ENV = "el .env"
ORIGEN_USUARIO = "~/.harness/config.toml"
# Cómo se eligió el modelo (ConfigModelo.origen).
ORIGEN_OPCION_PROVEEDOR = "--proveedor"
ORIGEN_OPCION_PERFIL = "--perfil"
ORIGEN_PERFIL_PREDETERMINADO = "predeterminado"
MENSAJE_FALTA_TOKEN_GITHUB = (
    "Falta el token de GitHub (GITHUB_TOKEN en el entorno o en ~/.harness/config.toml; se "
    "guarda con 'harness config --credencial GITHUB_TOKEN')."
)
MENSAJE_SIN_MODELO = (
    "No hay modelo configurado: ejecuta 'harness config' (o define HARNESS_PROVEEDOR y "
    "HARNESS_MODELO en el entorno)."
)

# Variable -> de dónde salió, para las que cargó el harness (las demás son del entorno).
_origenes: dict[str, str] = {}


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
    # De qué variable salió la key (para avisar si es demasiado corta).
    variable_api_key: str | None = None
    # El perfil de config.toml, si salió de uno.
    perfil: str | None = None
    # Cómo se eligió: --proveedor, --perfil, "predeterminado" o "HARNESS_PROVEEDOR del .env".
    origen: str | None = None


@dataclass(frozen=True)
class ConfigJira:
    """Sitio de Jira Cloud y credenciales para su API REST."""

    url: str
    email: str = field(repr=False)
    # repr=False para que el token no aparezca si alguien imprime o loguea la config.
    token: str = field(repr=False)


def cargar_env(ruta_env: Path | None = None) -> None:
    """Carga el .env y después las credenciales de config.toml en el entorno, sin pisar nada.

    El entorno real gana sobre el .env (load_dotenv no sobrescribe) y los dos sobre
    config.toml. Así las referencias ${NOMBRE} de harness.yaml y mcp.json, y Jira, encuentran
    las credenciales sin saber de dónde vienen. Lanza ErrorConfiguracion si config.toml no es
    válido.
    """
    antes = set(os.environ)
    # RUTA_ENV se lee aquí y no como valor por defecto: así los tests pueden cambiarla.
    load_dotenv(ruta_env or RUTA_ENV)
    for nombre in set(os.environ) - antes:
        _origenes.setdefault(nombre, ORIGEN_ENV)
    for nombre, valor in _variables_de_usuario(leer_usuario()).items():
        if not os.environ.get(nombre, "").strip():
            os.environ[nombre] = valor
            _origenes[nombre] = ORIGEN_USUARIO


def leer_usuario() -> ConfigUsuario:
    """~/.harness/config.toml ya validado (vacío si no existe)."""
    try:
        return leer_config_usuario()
    except ErrorConfigUsuario as error:
        raise ErrorConfiguracion(str(error)) from error


def origen_variable(nombre: str) -> str | None:
    """De dónde salió la variable: el entorno, el .env o config.toml (None si no está)."""
    if not os.environ.get(nombre, "").strip():
        return None
    return _origenes.get(nombre, ORIGEN_ENTORNO)


def secretos_de_usuario() -> list[str]:
    """Todas las credenciales de config.toml, aunque esta ejecución no las use: se ocultan."""
    try:
        return [valor for valor in leer_config_usuario().credenciales.values() if valor]
    except ErrorConfigUsuario:
        return []


def cargar_config(ruta_env: Path | None = None, perfil: str | None = None) -> ConfigModelo:
    """El modelo según el orden de prioridad, sin --proveedor/--modelo (ver elegir_modelo).

    Con `perfil` (--perfil), ese perfil de config.toml; si no, HARNESS_PROVEEDOR del entorno o
    del .env; si no, el perfil predeterminado de config.toml.
    """
    cargar_env(ruta_env)
    usuario = leer_usuario()
    if perfil is not None:
        return _desde_perfil(usuario, perfil, ORIGEN_OPCION_PERFIL)
    if os.environ.get(VAR_PROVEEDOR, "").strip():
        origen = f"{VAR_PROVEEDOR} {_de(origen_variable(VAR_PROVEEDOR) or ORIGEN_ENTORNO)}"
        if usuario.perfil:
            origen += f"; gana sobre el perfil '{usuario.perfil}' de config.toml"
        return replace(_desde_variables(), origen=origen)
    if usuario.perfil:
        return _desde_perfil(usuario, usuario.perfil, ORIGEN_PERFIL_PREDETERMINADO)
    raise ErrorConfiguracion(MENSAJE_SIN_MODELO)


def _desde_perfil(usuario: ConfigUsuario, nombre: str, origen: str) -> ConfigModelo:
    perfil = usuario.perfiles.get(nombre)
    if perfil is None:
        existentes = ", ".join(usuario.perfiles) or "ninguno"
        raise ErrorConfiguracion(
            f"No existe el perfil '{nombre}' en {ruta_config()} (perfiles: {existentes}). "
            "Créalo con 'harness perfil agregar'."
        )
    preset = PRESETS[perfil.proveedor]
    variable = perfil.api_key_env or preset.variable_api_key
    api_key = _leer_obligatoria(variable, f"la API key del perfil '{nombre}'")
    return ConfigModelo(
        perfil.proveedor, perfil.modelo, perfil.base_url or preset.base_url, api_key, variable,
        nombre, origen,
    )


def _desde_variables() -> ConfigModelo:
    """El modelo de HARNESS_PROVEEDOR / HARNESS_MODELO (entorno o .env)."""
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
    return ConfigModelo(proveedor, modelo, base_url, api_key, variable_api_key)


def leer_respaldo(actual: ConfigModelo | None = None) -> list[ConfigModelo]:
    """Los modelos de respaldo, en orden: HARNESS_RESPALDO (entorno o .env) o, si no está,
    `respaldo` de config.toml.

    Cada entrada es el nombre de un perfil (sin ':') o proveedor:modelo, separado en el primer
    ':' porque los modelos gratis de OpenRouter terminan en ':free'. Se quita el modelo
    `actual` si está en la lista: pasar a él sería repetir el que acaba de fallar. Lanza
    ErrorConfiguracion si una entrada no se entiende o le falta la API key.
    """
    usuario = leer_usuario()
    texto = os.environ.get(VAR_RESPALDO, "")
    entradas = [parte.strip() for parte in texto.split(SEPARADOR_RESPALDO) if parte.strip()]
    origen = VAR_RESPALDO
    if not texto.strip():
        entradas, origen = list(usuario.respaldo), ORIGEN_RESPALDO_USUARIO
    respaldo = []
    for entrada in entradas:
        config = leer_entrada(entrada, usuario, origen)
        if actual is None or (config.proveedor, config.modelo) != (actual.proveedor, actual.modelo):
            respaldo.append(config)
    return respaldo


def leer_entrada(entrada: str, usuario: ConfigUsuario, origen: str) -> ConfigModelo:
    """Un perfil de config.toml (sin ':') o proveedor:modelo (respaldo y revisor)."""
    if SEPARADOR_PROVEEDOR not in entrada:
        return _desde_perfil(usuario, entrada.strip(), origen)
    return _leer_entrada_respaldo(entrada, origen)


def elegir_modelo(
    base: ConfigModelo | None, proveedor: str | None, modelo: str | None
) -> ConfigModelo:
    """El modelo de --proveedor / --modelo, que ganan sobre todo; sin ellos, `base`.

    Sin --proveedor, --modelo cambia solo el modelo de `base` (que entonces no puede faltar).
    """
    if proveedor is None:
        if base is None:
            raise ErrorConfiguracion(MENSAJE_SIN_MODELO)
        return replace(base, modelo=modelo) if modelo else base
    if not modelo:
        raise ErrorConfiguracion("Con --proveedor hace falta también --modelo.")
    config = _leer_entrada_respaldo(f"{proveedor}:{modelo}", ORIGEN_OPCION_PROVEEDOR)
    return replace(config, origen=ORIGEN_OPCION_PROVEEDOR)


def _leer_entrada_respaldo(entrada: str, origen: str = VAR_RESPALDO) -> ConfigModelo:
    # Sin perfil detrás: personalizado no se admite, porque necesita su URL (un perfil sí).
    proveedor, separador, modelo = entrada.partition(SEPARADOR_PROVEEDOR)
    proveedor, modelo = proveedor.strip().lower(), modelo.strip()
    if not separador or not modelo:
        raise ErrorConfiguracion(
            f"'{entrada}' en {origen} no es proveedor:modelo "
            "(por ejemplo openrouter:qwen/qwen3-coder:free) ni el nombre de un perfil."
        )
    if proveedor not in PRESETS or proveedor == PROVEEDOR_PERSONALIZADO:
        conocidos = ", ".join(p for p in PRESETS if p != PROVEEDOR_PERSONALIZADO)
        raise ErrorConfiguracion(
            f"Proveedor '{proveedor}' no válido en {origen}. Opciones: {conocidos}."
        )
    preset = PRESETS[proveedor]
    variable = preset.variable_api_key
    api_key = _leer_obligatoria(variable, f"la API key de '{proveedor}' (de {origen})")
    return ConfigModelo(proveedor, modelo, preset.base_url, api_key, variable)


def ruta_mcp() -> Path:
    """El mcp.json que se usa: ~/.harness/mcp.json, o el de desarrollo si ese no existe."""
    usuario = ruta_mcp_usuario()
    return usuario if usuario.is_file() else RUTA_MCP_DESARROLLO


def leer_token_github() -> str | None:
    """Token de GitHub del .env (ya cargado) para crear el PR; None si no está."""
    return os.environ.get(VAR_TOKEN_GITHUB, "").strip() or None


def leer_config_jira() -> ConfigJira:
    """Sitio y credenciales de Jira del .env; lanza ErrorConfiguracion diciendo qué falta."""
    valores = {variable: os.environ.get(variable, "").strip() for variable in VARIABLES_JIRA}
    faltantes = [variable for variable, valor in valores.items() if not valor]
    if faltantes:
        raise ErrorConfiguracion(
            f"Para leer tickets de Jira falta {', '.join(faltantes)}: guárdalo con 'harness "
            "config' (o ponlo en el entorno o, en desarrollo, en el .env). El API token se crea "
            "en https://id.atlassian.com/manage-profile/security/api-tokens."
        )
    return ConfigJira(
        valores[VAR_JIRA_URL].rstrip("/"), valores[VAR_JIRA_EMAIL], valores[VAR_JIRA_TOKEN]
    )


def leer_herramientas_github_modelo() -> bool:
    """Si el modelo recibe las herramientas de GitHub por MCP (por defecto, sí)."""
    valor = os.environ.get(VAR_HERRAMIENTAS_GITHUB_MODELO, "").strip().lower()
    return valor not in VALORES_DESACTIVADO


def _variables_de_usuario(usuario: ConfigUsuario) -> dict[str, str]:
    variables = dict(usuario.credenciales)
    if usuario.jira.url:
        variables[VAR_JIRA_URL] = usuario.jira.url
    if usuario.jira.email:
        variables[VAR_JIRA_EMAIL] = usuario.jira.email
    return {nombre: valor for nombre, valor in variables.items() if valor}


def _de(origen: str) -> str:
    """'del .env', 'del entorno' o 'de ~/.harness/config.toml'."""
    return f"del {origen.removeprefix('el ')}" if origen.startswith("el ") else f"de {origen}"


def donde_configurar(variable: str) -> str:
    """La variable y de dónde salió ("GEMINI_API_KEY (viene de ~/.harness/config.toml)"), o
    cómo guardarla si no está: para que un mensaje no nombre solo el .env."""
    origen = origen_variable(variable)
    if origen is None:
        return (
            f"{variable} (no está: guárdala con 'harness config --credencial {variable}')"
        )
    return f"{variable} ({de_donde_viene(variable)})"


def de_donde_viene(variable: str) -> str:
    """'viene del .env', 'viene de ~/.harness/config.toml' o 'viene del entorno'."""
    return f"viene {_de(origen_variable(variable) or ORIGEN_ENTORNO)}"


def _leer_obligatoria(variable: str, que_es: str) -> str:
    valor = os.environ.get(variable, "").strip()
    if valor:
        return valor
    if variable.startswith(PREFIJO_VARIABLES_HARNESS):
        donde = "en el entorno o en el .env, o usa un perfil de 'harness config'"
    else:
        donde = (
            f"guárdala con 'harness config --credencial {variable}' (o ponla en el entorno o, "
            "en desarrollo, en el .env)"
        )
    raise ErrorConfiguracion(f"Falta {variable} ({que_es}): {donde}.")
