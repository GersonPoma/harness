"""Reintentos con espera, comunes a todos los proveedores.

Cada proveedor sabe qué errores de su SDK se resuelven esperando (429, 5xx, sin conexión) y los
describe con un ErrorPasajero; el resto (la espera, el tope de reintentos, el aviso de cada
reintento, el intervalo mínimo entre pedidos y el ErrorProveedorCaido final) es igual para
todos.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from harness.proveedores.base import (
    MOTIVO_CAIDO,
    MOTIVO_CUOTA_DIARIA,
    MOTIVO_LIMITE,
    ErrorProveedorCaido,
)

MAX_REINTENTOS = 5
ESPERA_INICIAL_SEGUNDOS = 5.0
ESPERA_MAXIMA_SEGUNDOS = 60.0
SUGERENCIA_OTRO_PROVEEDOR = (
    "configura un respaldo (respaldo en ~/.harness/config.toml o HARNESS_RESPALDO) o usa otro "
    "modelo (--perfil, o --proveedor y --modelo)."
)

T = TypeVar("T")


@dataclass(frozen=True)
class ErrorPasajero:
    """Un error que puede resolverse esperando, en palabras del proveedor."""

    # "saturado (503)", "sin conexión", "con límite de pedidos (429)"...
    descripcion: str
    es_limite: bool = False
    # Un 429 de cuota diaria no se recupera esperando: no se reintenta.
    es_cuota_diaria: bool = False
    # Retry-After del servidor, si lo mandó (ya recortado a ESPERA_MAXIMA_SEGUNDOS).
    espera_sugerida: float | None = None


class Reintentos:
    """Repite un pedido ante errores pasajeros, con espera creciente."""

    def __init__(
        self,
        proveedor: str,
        max_reintentos: int = MAX_REINTENTOS,
        dormir: Callable[[float], None] = time.sleep,
        reloj: Callable[[], float] = time.monotonic,
        al_reintentar: Callable[[str], None] | None = None,
        intervalo_minimo: float = 0.0,
    ) -> None:
        self._proveedor = proveedor
        self.max_reintentos = max_reintentos
        self._dormir = dormir
        self._reloj = reloj
        # Avisa de cada reintento para que una espera larga no parezca un cuelgue.
        self._al_reintentar = al_reintentar
        # Segundos mínimos entre dos pedidos (Gemini gratis permite 15 por minuto).
        self._intervalo_minimo = intervalo_minimo
        self._ultimo_pedido: float | None = None

    def pedir(
        self,
        pedido: Callable[[], T],
        clasificar: Callable[[BaseException], ErrorPasajero | None],
        tipos: tuple[type[BaseException], ...],
    ) -> T:
        """El resultado de `pedido`; un error de `tipos` que `clasificar` no reconoce como
        pasajero se propaga tal cual (el proveedor lo traduce)."""
        espera = ESPERA_INICIAL_SEGUNDOS
        reintento = 0
        while True:
            self._respetar_intervalo()
            try:
                return pedido()
            except tipos as error:
                pasajero = clasificar(error)
                if pasajero is None:
                    raise
                if pasajero.es_cuota_diaria:
                    raise self._error_cuota_diaria() from error
                if reintento == self.max_reintentos:
                    raise self._error_agotado(pasajero) from error
                reintento += 1
                segundos = pasajero.espera_sugerida or espera
                self.avisar(
                    f"{self._proveedor.capitalize()} {pasajero.descripcion}, "
                    f"reintento {reintento}/{self.max_reintentos} en {segundos:g} s..."
                )
                self._dormir(segundos)
                espera = min(espera * 2, ESPERA_MAXIMA_SEGUNDOS)

    def avisar(self, mensaje: str) -> None:
        """Un aviso para la consola (reintentos y pedidos repetidos)."""
        if self._al_reintentar:
            self._al_reintentar(mensaje)

    def _respetar_intervalo(self) -> None:
        if self._intervalo_minimo <= 0:
            return
        ahora = self._reloj()
        if self._ultimo_pedido is not None:
            faltante = self._intervalo_minimo - (ahora - self._ultimo_pedido)
            if faltante > 0:
                self._dormir(faltante)
                ahora += faltante
        self._ultimo_pedido = ahora

    def _error_agotado(self, pasajero: ErrorPasajero) -> ErrorProveedorCaido:
        reintentos = self.max_reintentos
        if pasajero.es_limite:
            return ErrorProveedorCaido(
                f"Se agotó el límite de pedidos del proveedor '{self._proveedor}' (error 429) "
                f"después de {reintentos} reintentos. Espera un rato o "
                f"{SUGERENCIA_OTRO_PROVEEDOR}",
                MOTIVO_LIMITE,
            )
        return ErrorProveedorCaido(
            f"El servicio de '{self._proveedor}' no está disponible ({pasajero.descripcion}) "
            f"después de {reintentos} reintentos. Intenta más tarde o {SUGERENCIA_OTRO_PROVEEDOR}",
            MOTIVO_CAIDO,
        )

    def _error_cuota_diaria(self) -> ErrorProveedorCaido:
        return ErrorProveedorCaido(
            f"Se agotó la cuota diaria del proveedor '{self._proveedor}' (error 429): no se "
            "recupera esperando unos minutos, se renueva una vez por día. Mañana, o ahora mismo "
            f"si {SUGERENCIA_OTRO_PROVEEDOR}",
            MOTIVO_CUOTA_DIARIA,
        )


def espera_de_retry_after(valor: str | None) -> float | None:
    """Los segundos de un header Retry-After, recortados a ESPERA_MAXIMA_SEGUNDOS."""
    try:
        return min(float(valor), ESPERA_MAXIMA_SEGUNDOS) if valor else None
    except ValueError:
        return None
