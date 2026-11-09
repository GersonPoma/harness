"""Respaldo entre proveedores: si uno se cae, la misma conversación sigue con el siguiente.

Los mensajes son tipos propios, así que el siguiente recibe la lista tal cual y repite el
pedido que falló. Solo se pasa al siguiente con ErrorProveedorCaido (no responde después de
los reintentos, o agotó la cuota diaria); los errores de configuración cortan igual que sin
respaldo. No se vuelve atrás: si cae el último, se corta y queda la sesión para resume.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from harness.proveedores.base import ErrorProveedorCaido, ProveedorLLM
from harness.tipos import Herramienta, Mensaje, RespuestaModelo


@dataclass(frozen=True)
class CambioProveedor:
    """Se pasó de un proveedor al siguiente: cuál, a cuál y por qué."""

    anterior: str
    siguiente: str
    motivo: str
    detalle: str


class ProveedorConRespaldo:
    """Cumple ProveedorLLM y usa el primero de la lista que responda."""

    def __init__(
        self,
        proveedores: list[tuple[str, ProveedorLLM]],
        al_cambiar: Callable[[CambioProveedor], None] | None = None,
    ) -> None:
        if not proveedores:
            raise ValueError("Hace falta al menos un proveedor.")
        self._proveedores = proveedores
        self._indice = 0
        self._al_cambiar = al_cambiar
        self.usados = [proveedores[0][0]]

    @property
    def actual(self) -> str:
        """El proveedor y modelo que responde ahora ("gemini/<modelo>")."""
        return self._proveedores[self._indice][0]

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[Herramienta],
        herramienta_obligatoria: str | None = None,
    ) -> RespuestaModelo:
        """Responde con el proveedor actual; si se cae, pasa al siguiente y repite el pedido."""
        while True:
            proveedor = self._proveedores[self._indice][1]
            try:
                return proveedor.responder(mensajes, herramientas, herramienta_obligatoria)
            except ErrorProveedorCaido as error:
                if self._indice + 1 >= len(self._proveedores):
                    raise
                self._pasar_al_siguiente(error)

    def adaptar_esquema(self, esquema: dict[str, Any]) -> dict[str, Any]:
        """El esquema como lo acepta el proveedor que responde ahora."""
        return self._proveedores[self._indice][1].adaptar_esquema(esquema)

    def _pasar_al_siguiente(self, error: ErrorProveedorCaido) -> None:
        anterior = self.actual
        self._indice += 1
        self.usados.append(self.actual)
        if self._al_cambiar:
            self._al_cambiar(CambioProveedor(anterior, self.actual, error.motivo, str(error)))
