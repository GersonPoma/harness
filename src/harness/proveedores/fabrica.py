"""Fábrica que elige la clase de proveedor según el preset configurado."""

from collections.abc import Callable, Sequence

from harness.config import ConfigModelo
from harness.proveedores.anthropic import ProveedorAnthropic
from harness.proveedores.base import ProveedorLLM
from harness.proveedores.openai_compatible import ProveedorOpenAICompatible
from harness.proveedores.presets import PRESETS, FormatoApi
from harness.proveedores.reintentos import MAX_REINTENTOS
from harness.proveedores.respaldo import CambioProveedor, ProveedorConRespaldo

# Con otro proveedor detrás no conviene esperar los 5 reintentos (~2,5 minutos).
REINTENTOS_CON_RESPALDO = 2


def crear_proveedor(
    config: ConfigModelo,
    al_reintentar: Callable[[str], None] | None = None,
    respaldo: Sequence[ConfigModelo] = (),
    al_cambiar: Callable[[CambioProveedor], None] | None = None,
) -> ProveedorLLM:
    """El proveedor configurado; con `respaldo`, una cadena que pasa al siguiente si se cae."""
    if not respaldo:
        return _crear_uno(config, al_reintentar, MAX_REINTENTOS)
    cadena = [config, *respaldo]
    proveedores: list[tuple[str, ProveedorLLM]] = []
    for indice, config_modelo in enumerate(cadena):
        # El último de la lista mantiene todos los reintentos: después no hay nadie.
        es_ultimo = indice == len(cadena) - 1
        reintentos = MAX_REINTENTOS if es_ultimo else REINTENTOS_CON_RESPALDO
        proveedor = _crear_uno(config_modelo, al_reintentar, reintentos)
        proveedores.append((identificador(config_modelo), proveedor))
    return ProveedorConRespaldo(proveedores, al_cambiar)


def identificador(config: ConfigModelo) -> str:
    """'proveedor/modelo': cómo se nombra un modelo en avisos, log y sesiones."""
    return f"{config.proveedor}/{config.modelo}"


def _crear_uno(
    config: ConfigModelo, al_reintentar: Callable[[str], None] | None, max_reintentos: int
) -> ProveedorLLM:
    formato = PRESETS[config.proveedor].formato
    if formato == FormatoApi.OPENAI:
        return ProveedorOpenAICompatible(
            config, al_reintentar=al_reintentar, max_reintentos=max_reintentos
        )
    if formato == FormatoApi.ANTHROPIC:
        return ProveedorAnthropic(
            config, al_reintentar=al_reintentar, max_reintentos=max_reintentos
        )
    raise ValueError(f"Formato de API sin implementar: {formato}")
