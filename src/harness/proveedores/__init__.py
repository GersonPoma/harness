"""Proveedores de modelos y la fábrica que elige cuál usar."""

from harness.config import ConfigModelo
from harness.proveedores.base import ErrorProveedor, ProveedorLLM
from harness.proveedores.openai_compatible import ProveedorOpenAICompatible
from harness.proveedores.presets import PRESETS, FormatoApi

__all__ = ["ErrorProveedor", "ProveedorLLM", "crear_proveedor"]


def crear_proveedor(config: ConfigModelo) -> ProveedorLLM:
    """Crea el proveedor que corresponde al formato de API del preset configurado."""
    formato = PRESETS[config.proveedor].formato
    if formato == FormatoApi.OPENAI:
        return ProveedorOpenAICompatible(config)
    raise ValueError(f"Formato de API sin implementar: {formato}")
