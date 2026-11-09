"""Interfaz común de todos los proveedores de modelos."""

from typing import Any, Protocol

from harness.tipos import Herramienta, Mensaje, RespuestaModelo


class ErrorProveedor(Exception):
    """Error del proveedor que no se puede resolver reintentando."""


# Por qué un proveedor quedó fuera de servicio para esta ejecución.
MOTIVO_CAIDO = "caido"
MOTIVO_LIMITE = "limite"
MOTIVO_CUOTA_DIARIA = "cuota_diaria"


class ErrorProveedorCaido(ErrorProveedor):
    """El proveedor no responde después de los reintentos, o agotó su cuota diaria.

    Es el único error con el que el respaldo pasa al siguiente proveedor: los demás (API key,
    modelo inexistente, pedido inválido) son de configuración y otro proveedor no los arregla.
    """

    def __init__(self, mensaje: str, motivo: str) -> None:
        super().__init__(mensaje)
        self.motivo = motivo


class ProveedorLLM(Protocol):
    """Lo único que el bucle sabe de un modelo: recibe mensajes y responde."""

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[Herramienta],
        herramienta_obligatoria: str | None = None,
    ) -> RespuestaModelo:
        """La respuesta del modelo a la conversación.

        `herramienta_obligatoria` pide que el modelo llame a esa herramienta (tool_choice); el
        proveedor que no lo soporta lo ignora, y el modelo elige como siempre.
        """
        ...

    def adaptar_esquema(self, esquema: dict[str, Any]) -> dict[str, Any]:
        """El esquema de una herramienta tal como lo acepta la API de este proveedor.

        Las herramientas guardan el esquema original (el de un servidor MCP, por ejemplo) y
        cada proveedor lo adapta justo antes de mandarlo: cada API acepta cosas distintas.
        """
        ...
