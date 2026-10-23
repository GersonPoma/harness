"""Carga y validación de harness.yaml del repo objetivo (docs/harness-yaml.md)."""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import ErrorDetails

NOMBRE_ARCHIVO = "harness.yaml"
VERSION_SOPORTADA = 1
PATRON_NOMBRE = r"^[a-z0-9-]+$"

Entero = Annotated[int, Field(ge=1)]
Nombre = Annotated[str, Field(pattern=PATRON_NOMBRE)]


class ErrorConfigProyecto(Exception):
    """harness.yaml no se puede leer o no cumple la especificación."""


class _Modelo(BaseModel):
    # extra="forbid": una clave desconocida es un error (detecta errores de tipeo).
    # strict=True: "30" no se acepta donde va un entero, ni 5432 donde va un texto.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ConfigDocker(_Modelo):
    """Contenedor donde corre el proyecto."""

    imagen: str
    directorio_trabajo: str = "/app"
    instalar: list[str] = []
    reinstalar_si_cambia: list[str] = []

    @field_validator("instalar", mode="before")
    @classmethod
    def _texto_a_lista(cls, valor: object) -> object:
        return [valor] if isinstance(valor, str) else valor


class ConfigComandos(_Modelo):
    """Comandos con los que se valida el código."""

    tests: str
    lint: str | None = None


class ConfigServicio(_Modelo):
    """Contenedor extra que necesita el proyecto (base de datos, Redis...)."""

    nombre: Nombre
    imagen: str
    variables: dict[str, str] = {}
    listo_cuando: str | None = None
    timeout_listo_segundos: Entero = 60


class ConfigGit(_Modelo):
    """Ramas y remoto para la entrega."""

    rama_base: str = "main"
    prefijo_rama: str = "feature/"
    remoto: str = "origin"


class ConfigLimites(_Modelo):
    """Límites de la ejecución."""

    max_pasos: Entero = 30
    max_intentos_validacion: Entero = 3
    timeout_comando_segundos: Entero = 120
    max_caracteres_salida: Entero = 4000


class ConfigProyecto(_Modelo):
    """Contenido validado de harness.yaml."""

    version: int
    proyecto: Nombre
    docker: ConfigDocker
    comandos: ConfigComandos
    comandos_permitidos: list[str] = []
    servicios: list[ConfigServicio] = []
    variables_entorno: dict[str, str] = {}
    git: ConfigGit = ConfigGit()
    limites: ConfigLimites = ConfigLimites()
    archivos_protegidos: list[str] = []

    @field_validator("version")
    @classmethod
    def _version_soportada(cls, valor: int) -> int:
        if valor != VERSION_SOPORTADA:
            raise ValueError("versión de harness.yaml no soportada")
        return valor

    @field_validator("servicios")
    @classmethod
    def _servicios_unicos(cls, valor: list[ConfigServicio]) -> list[ConfigServicio]:
        nombres = [servicio.nombre for servicio in valor]
        repetidos = sorted({nombre for nombre in nombres if nombres.count(nombre) > 1})
        if repetidos:
            raise ValueError(f"servicios repetidos: {', '.join(repetidos)}")
        return valor


def cargar_config_proyecto(repo: Path) -> ConfigProyecto | None:
    """Lee y valida harness.yaml; devuelve None si el repo no lo tiene."""
    ruta = repo / NOMBRE_ARCHIVO
    if not ruta.is_file():
        return None
    return validar_texto_config(ruta.read_text(encoding="utf-8"))


def validar_texto_config(texto: str) -> ConfigProyecto:
    """Valida el contenido de un harness.yaml (también uno propuesto que aún no se escribió)."""
    try:
        # safe_load y nunca load: load puede ejecutar código escrito en el YAML.
        datos = yaml.safe_load(texto)
    except yaml.YAMLError as error:
        raise ErrorConfigProyecto(f"{NOMBRE_ARCHIVO} no es YAML válido: {error}") from error
    if not isinstance(datos, dict):
        raise ErrorConfigProyecto(f"{NOMBRE_ARCHIVO} debe ser un objeto con claves.")
    try:
        config = ConfigProyecto.model_validate(datos)
    except ValidationError as error:
        raise ErrorConfigProyecto(_formatear_errores(error)) from error
    return config


def _formatear_errores(error: ValidationError) -> str:
    lineas = [f"{NOMBRE_ARCHIVO} tiene errores:"]
    for detalle in error.errors():
        lineas.append(f"  {_ruta_error(detalle['loc'])}: {_mensaje_error(detalle)}")
    return "\n".join(lineas)


def _ruta_error(ubicacion: tuple[int | str, ...]) -> str:
    """('servicios', 0, 'nombre') -> 'servicios[0].nombre'."""
    ruta = ""
    for parte in ubicacion:
        ruta += f"[{parte}]" if isinstance(parte, int) else f".{parte}"
    return ruta.lstrip(".") or "(raíz)"


def _mensaje_error(detalle: ErrorDetails) -> str:
    tipo = detalle["type"]
    if tipo == "missing":
        return "falta este campo"
    if tipo == "extra_forbidden":
        return "clave desconocida"
    if tipo == "value_error":
        return str(detalle["ctx"]["error"])
    if tipo == "string_pattern_mismatch":
        return "solo se permiten minúsculas, números y guiones"
    return f"{detalle['msg']} (valor: {detalle.get('input')!r})"
