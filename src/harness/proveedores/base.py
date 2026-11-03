"""Interfaz común de todos los proveedores de modelos."""

from typing import Any, Protocol

from harness.tipos import Herramienta, Mensaje, RespuestaModelo


class ErrorProveedor(Exception):
    """Error del proveedor que no se puede resolver reintentando."""


class ProveedorLLM(Protocol):
    """Lo único que el bucle sabe de un modelo: recibe mensajes y responde."""

    def responder(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> RespuestaModelo: ...

    def adaptar_esquema(self, esquema: dict[str, Any]) -> dict[str, Any]:
        """El esquema de una herramienta tal como lo acepta la API de este proveedor.

        Las herramientas guardan el esquema original (el de un servidor MCP, por ejemplo) y
        cada proveedor lo adapta justo antes de mandarlo: cada API acepta cosas distintas.
        """
        ...
