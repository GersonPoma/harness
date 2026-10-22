"""Fábrica que elige la clase de proveedor según el preset configurado."""

from collections.abc import Callable

from harness.config import ConfigModelo
from harness.proveedores.base import ProveedorLLM
from harness.proveedores.openai_compatible import ProveedorOpenAICompatible
from harness.proveedores.presets import PRESETS, FormatoApi


def crear_proveedor(
    config: ConfigModelo, al_reintentar: Callable[[str], None] | None = None
) -> ProveedorLLM:
    """Crea el proveedor que corresponde al formato de API del preset configurado."""
    formato = PRESETS[config.proveedor].formato
    if formato == FormatoApi.OPENAI:
        return ProveedorOpenAICompatible(config, al_reintentar=al_reintentar)
    raise ValueError(f"Formato de API sin implementar: {formato}")
