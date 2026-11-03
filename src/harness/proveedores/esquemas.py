"""Transformaciones de JSON Schema que los proveedores usan al adaptar los esquemas de las
herramientas (por ejemplo, los que vienen de un servidor MCP) a lo que acepta su API.

Cada proveedor elige cuáles aplica en su adaptar_esquema: cada API acepta cosas distintas.
"""

import copy
from typing import Any

PREFIJO_EXTENSION = "x-"
CLAVES_DEFINICIONES = ("$defs", "definitions")
PREFIJOS_REFERENCIA = ("#/$defs/", "#/definitions/")
# Claves cuyos valores son esquemas con nombre: los nombres son datos, no palabras de JSON
# Schema (una propiedad puede llamarse "x-algo" y no es una extensión).
CLAVES_CON_NOMBRES = ("properties", "patternProperties", *CLAVES_DEFINICIONES)
# Si una referencia se vuelve a usar a sí misma, se corta ahí con un objeto sin detalle.
ESQUEMA_RECURSIVO: dict[str, Any] = {"type": "object"}


def quitar_extensiones(esquema: Any) -> Any:
    """Quita las claves de extensión "x-..." (metadatos del servidor, como x-mcp-header)."""
    if isinstance(esquema, list):
        return [quitar_extensiones(valor) for valor in esquema]
    if not isinstance(esquema, dict):
        return esquema
    resultado: dict[str, Any] = {}
    for clave, valor in esquema.items():
        if clave.startswith(PREFIJO_EXTENSION):
            continue
        if clave in CLAVES_CON_NOMBRES and isinstance(valor, dict):
            resultado[clave] = {nombre: quitar_extensiones(v) for nombre, v in valor.items()}
        else:
            resultado[clave] = quitar_extensiones(valor)
    return resultado


def resolver_referencias(esquema: dict[str, Any]) -> dict[str, Any]:
    """Reemplaza cada $ref interno por su definición y quita $defs/definitions."""
    definiciones: dict[str, Any] = {}
    for clave in CLAVES_DEFINICIONES:
        definiciones.update(esquema.get(clave, {}))
    sin_definiciones = {k: v for k, v in esquema.items() if k not in CLAVES_DEFINICIONES}
    return _reemplazar(sin_definiciones, definiciones, ())


def _reemplazar(valor: Any, definiciones: dict[str, Any], visitadas: tuple[str, ...]) -> Any:
    if isinstance(valor, list):
        return [_reemplazar(v, definiciones, visitadas) for v in valor]
    if not isinstance(valor, dict):
        return valor
    referencia = valor.get("$ref")
    nombre = _nombre_referencia(referencia)
    if nombre is not None and nombre in definiciones:
        if nombre in visitadas:
            return copy.deepcopy(ESQUEMA_RECURSIVO)
        resuelto = _reemplazar(definiciones[nombre], definiciones, (*visitadas, nombre))
        # Lo que acompaña al $ref (por ejemplo una descripción) se conserva.
        extra = {k: v for k, v in valor.items() if k != "$ref"}
        return {**resuelto, **_reemplazar(extra, definiciones, visitadas)}
    return {k: _reemplazar(v, definiciones, visitadas) for k, v in valor.items()}


def _nombre_referencia(referencia: Any) -> str | None:
    if not isinstance(referencia, str):
        return None
    for prefijo in PREFIJOS_REFERENCIA:
        if referencia.startswith(prefijo):
            return referencia.removeprefix(prefijo)
    return None
