"""Interfaz común de todos los proveedores de modelos."""

from typing import Protocol

from harness.tipos import Herramienta, Mensaje, RespuestaModelo


class ErrorProveedor(Exception):
    """Error del proveedor que no se puede resolver reintentando."""


class ProveedorLLM(Protocol):
    """Lo único que el bucle sabe de un modelo: recibe mensajes y responde."""

    def responder(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> RespuestaModelo: ...
