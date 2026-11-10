"""Configuración por usuario: ~/.harness/config.toml (perfiles, respaldo, revisor y credenciales).

Vive en la carpeta del usuario, fuera de cualquier repo, así nunca llega a Git. Lo escriben
'harness config' y 'harness perfil' (se puede editar a mano, pero se regenera entero: los
comentarios no se conservan) y se lee con tomllib. Las credenciales se cargan en el entorno del
proceso con menos prioridad que el entorno real y el .env (ver config.cargar_env).
"""

import json
import os
from importlib import resources
import re
import stat
import tempfile
import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from harness.proveedores.presets import PRESETS, PROVEEDOR_PERSONALIZADO

# Una función y no una constante armada al importar: los tests la apuntan a una carpeta temporal.
CARPETA_USUARIO = Path.home() / ".harness"
NOMBRE_CONFIG = "config.toml"
NOMBRE_MCP = "mcp.json"
# Viaja dentro del paquete: quien lo instala con uv tool install no tiene el repo.
PLANTILLA_MCP = "plantillas/mcp.example.json"
PATRON_PERFIL = re.compile(r"[a-z0-9_-]+")
PATRON_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SEPARADOR_PROVEEDOR = ":"
# Solo el usuario: en Linux y macOS (en Windows hereda los permisos de la carpeta del usuario).
PERMISOS_ARCHIVO = 0o600
PERMISOS_CARPETA = 0o700
PERMISOS_DE_OTROS = stat.S_IRWXG | stat.S_IRWXO
# Un secreto se muestra como "****" más sus últimos caracteres, y solo si es largo: así se
# reconoce cuál es sin poder copiarlo.
MIN_CARACTERES_CON_FINAL = 12
CARACTERES_VISIBLES = 4
TEXTO_OCULTO = "****"
ENCABEZADO = (
    "# Lo escriben 'harness config' y 'harness perfil'. Tiene tus API keys: no lo copies a un "
    "repo.\n"
)


class ErrorConfigUsuario(Exception):
    """config.toml no se pudo leer o no es válido."""


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Perfil(_Modelo):
    """Un modelo con nombre: proveedor (preset) y modelo; personalizado, también URL y key."""

    proveedor: str
    modelo: str = Field(min_length=1)
    base_url: str | None = None
    api_key_env: str | None = None

    @field_validator("proveedor")
    @classmethod
    def _proveedor_conocido(cls, valor: str) -> str:
        valor = valor.lower()
        if valor not in PRESETS:
            raise ValueError(f"proveedor desconocido; opciones: {', '.join(PRESETS)}")
        return valor

    @model_validator(mode="after")
    def _datos_de_personalizado(self) -> Self:
        es_personalizado = self.proveedor == PROVEEDOR_PERSONALIZADO
        if es_personalizado and not (self.base_url and self.api_key_env):
            raise ValueError("un perfil personalizado necesita base_url y api_key_env")
        if not es_personalizado and (self.base_url or self.api_key_env):
            raise ValueError("base_url y api_key_env son solo para proveedor = personalizado")
        if self.api_key_env and not PATRON_VARIABLE.fullmatch(self.api_key_env):
            raise ValueError("api_key_env no es un nombre de variable válido")
        return self


class JiraUsuario(_Modelo):
    """Sitio y email de Jira: no son secretos (el token va en credenciales)."""

    url: str | None = None
    email: str | None = None


class ConfigUsuario(_Modelo):
    """Todo config.toml ya validado; vacío si el archivo no existe."""

    perfil: str | None = None
    respaldo: list[str] = []
    revisor: str | None = None
    perfiles: dict[str, Perfil] = {}
    credenciales: dict[str, str] = {}
    jira: JiraUsuario = JiraUsuario()

    @model_validator(mode="after")
    def _referencias(self) -> Self:
        for nombre in self.perfiles:
            if not PATRON_PERFIL.fullmatch(nombre):
                raise ValueError(f"perfiles.{nombre}: el nombre solo admite [a-z0-9_-]")
        for variable in self.credenciales:
            if not PATRON_VARIABLE.fullmatch(variable):
                raise ValueError(f"credenciales.{variable}: no es un nombre de variable válido")
        if self.perfil is not None and self.perfil not in self.perfiles:
            raise ValueError(f"perfil: no existe el perfil '{self.perfil}'")
        for indice, entrada in enumerate(self.respaldo):
            self._perfil_o_modelo(entrada, f"respaldo[{indice}]")
        if self.revisor is not None:
            self._perfil_o_modelo(self.revisor, "revisor")
        return self

    def _perfil_o_modelo(self, entrada: str, donde: str) -> None:
        if SEPARADOR_PROVEEDOR not in entrada and entrada not in self.perfiles:
            raise ValueError(
                f"{donde}: '{entrada}' no es un perfil ni proveedor:modelo"
            )


def ruta_config() -> Path:
    """~/.harness/config.toml."""
    return CARPETA_USUARIO / NOMBRE_CONFIG


def ruta_mcp_usuario() -> Path:
    """~/.harness/mcp.json."""
    return CARPETA_USUARIO / NOMBRE_MCP


def plantilla_mcp() -> str:
    """El mcp.json de ejemplo (GitHub y Jira, por Docker y remotos), sin secretos."""
    return resources.files("harness").joinpath(PLANTILLA_MCP).read_text(encoding="utf-8")


def crear_mcp_usuario() -> Path:
    """Copia la plantilla a ~/.harness/mcp.json; no pisa uno que ya exista."""
    ruta = ruta_mcp_usuario()
    if ruta.exists():
        raise ErrorConfigUsuario(f"{ruta} ya existe: no se pisa.")
    _escribir_privado(ruta, plantilla_mcp())
    return ruta


def leer_config_usuario(ruta: Path | None = None) -> ConfigUsuario:
    """Lee y valida config.toml; si no existe, una configuración vacía."""
    ruta = ruta or ruta_config()
    if not ruta.is_file():
        return ConfigUsuario()
    try:
        datos = tomllib.loads(ruta.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as error:
        raise ErrorConfigUsuario(f"{ruta} no es un TOML válido: {error}") from error
    try:
        return ConfigUsuario.model_validate(datos)
    except ValidationError as error:
        raise ErrorConfigUsuario(f"{ruta} tiene errores:\n{_describir(error)}") from error


def guardar_config_usuario(config: ConfigUsuario, ruta: Path | None = None) -> Path:
    """Escribe config.toml de una vez (un temporal y os.replace), solo para el usuario."""
    ruta = ruta or ruta_config()
    ConfigUsuario.model_validate(config.model_dump())
    _escribir_privado(ruta, a_toml(config))
    return ruta


def a_toml(config: ConfigUsuario) -> str:
    """config.toml como texto, en un orden fijo."""
    lineas = [ENCABEZADO.rstrip("\n")]
    if config.perfil:
        lineas.append(f"perfil = {_texto(config.perfil)}")
    if config.respaldo:
        lineas.append(f"respaldo = [{', '.join(_texto(e) for e in config.respaldo)}]")
    if config.revisor:
        lineas.append(f"revisor = {_texto(config.revisor)}")
    for nombre, perfil in config.perfiles.items():
        lineas += ["", f"[perfiles.{nombre}]"]
        lineas += [
            f"{clave} = {_texto(valor)}"
            for clave, valor in perfil.model_dump().items() if valor is not None
        ]
    if config.credenciales:
        lineas += ["", "[credenciales]"]
        lineas += [f"{nombre} = {_texto(valor)}" for nombre, valor in config.credenciales.items()]
    jira = {clave: valor for clave, valor in config.jira.model_dump().items() if valor}
    if jira:
        lineas += ["", "[jira]"]
        lineas += [f"{clave} = {_texto(valor)}" for clave, valor in jira.items()]
    return "\n".join(lineas) + "\n"


def enmascarar(valor: str) -> str:
    """'****' más los últimos 4 caracteres (solo si el valor es largo): nunca el secreto."""
    if len(valor) >= MIN_CARACTERES_CON_FINAL:
        return f"{TEXTO_OCULTO}{valor[-CARACTERES_VISIBLES:]}"
    return TEXTO_OCULTO


def aviso_permisos(ruta: Path | None = None) -> str | None:
    """En Linux y macOS, si otros usuarios pueden leer config.toml."""
    ruta = ruta or ruta_config()
    if os.name != "posix" or not ruta.is_file():
        return None
    if ruta.stat().st_mode & PERMISOS_DE_OTROS:
        return (
            f"{ruta} lo pueden leer otros usuarios y tiene tus API keys: "
            f"chmod 600 {ruta}"
        )
    return None


def _escribir_privado(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(ruta.parent, PERMISOS_CARPETA)
    # mkstemp crea el archivo con 0600: el secreto nunca queda legible para otros, ni un
    # instante.
    descriptor, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
            archivo.write(texto)
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    if os.name == "posix":
        os.chmod(ruta, PERMISOS_ARCHIVO)


def _texto(valor: str) -> str:
    # Un string de JSON es un string básico válido de TOML (mismos escapes), salvo DEL.
    return json.dumps(valor, ensure_ascii=False).replace("\x7f", "\\u007f")


def _describir(error: ValidationError) -> str:
    lineas = []
    for detalle in error.errors():
        ruta = ".".join(str(parte) for parte in detalle["loc"])
        mensaje = detalle["msg"].removeprefix("Value error, ")
        lineas.append(f"  {ruta}: {mensaje}" if ruta else f"  {mensaje}")
    return "\n".join(lineas)
