"""Fábrica que elige la clase de proveedor según el preset configurado."""

from harness.config import ConfigModelo
from harness.proveedores.base import ProveedorLLM
from harness.proveedores.openai_compatible import ProveedorOpenAICompatible
from harness.proveedores.presets import PRESETS, FormatoApi


def crear_proveedor(config: ConfigModelo) -> ProveedorLLM:
    """Crea el proveedor que corresponde al formato de API del preset configurado."""
    formato = PRESETS[config.proveedor].formato
    if formato == FormatoApi.OPENAI:
        return ProveedorOpenAICompatible(config)
    raise ValueError(f"Formato de API sin implementar: {formato}")
