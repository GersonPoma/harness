"""Tipos propios del harness, independientes de cualquier SDK de modelos."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Rol(StrEnum):
    """Quién emite un mensaje dentro de la conversación."""

    SISTEMA = "sistema"
    USUARIO = "usuario"
    ASISTENTE = "asistente"
    HERRAMIENTA = "herramienta"


@dataclass
class LlamadaHerramienta:
    """Pedido del modelo para ejecutar una herramienta."""

    id: str
    nombre: str
    argumentos: dict[str, Any]
    # Los modelos chicos a veces mandan JSON mal armado; se marca aquí para devolverle el
    # error al modelo en vez de cortar la ejecución.
    error_argumentos: str | None = None


@dataclass
class Mensaje:
    """Un mensaje de la conversación con el modelo."""

    rol: Rol
    contenido: str | None = None
    llamadas: list[LlamadaHerramienta] = field(default_factory=list)
    id_llamada: str | None = None


@dataclass
class Herramienta:
    """Herramienta que se le ofrece al modelo y la función que la ejecuta."""

    nombre: str
    descripcion: str
    parametros: dict[str, Any]
    funcion: Callable[..., str]


@dataclass
class RespuestaModelo:
    """Respuesta del modelo; sin llamadas significa que terminó."""

    texto: str | None
    llamadas: list[LlamadaHerramienta]
    tokens_entrada: int
    tokens_salida: int
