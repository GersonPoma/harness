import json
from pathlib import Path
from typing import Any

import pytest

from harness.config import ConfigModelo
from harness.proveedores.esquemas import quitar_extensiones, resolver_referencias
from harness.proveedores.openai_compatible import ProveedorOpenAICompatible
from harness.proveedores.presets import PRESETS
from harness.tipos import Herramienta
from tests.test_openai_compatible import ClienteFalso, respuesta_openai

# input_schema reales del servidor MCP de GitHub (tools/list, v2.0.3).
ESQUEMAS_GITHUB: dict[str, Any] = json.loads(
    (Path(__file__).parent / "datos" / "esquemas_github.json").read_text(encoding="utf-8")
)
CON_REFERENCIAS = {
    "type": "object",
    "properties": {
        "autor": {"$ref": "#/$defs/Persona", "description": "Quién lo escribió"},
        "revisores": {"type": "array", "items": {"$ref": "#/definitions/Persona"}},
    },
    "$defs": {"Persona": {"type": "object", "properties": {"login": {"type": "string"}}}},
    "definitions": {"Persona": {"type": "object", "properties": {"login": {"type": "string"}}}},
}


def _claves(valor: Any) -> set[str]:
    """Todas las claves del esquema, a cualquier profundidad (sin los nombres de propiedades)."""
    if isinstance(valor, list):
        return set().union(*(_claves(v) for v in valor)) if valor else set()
    if not isinstance(valor, dict):
        return set()
    claves = set(valor)
    for clave, interior in valor.items():
        if clave == "properties":
            claves |= set().union(*(_claves(v) for v in interior.values())) if interior else set()
        else:
            claves |= _claves(interior)
    return claves


def _proveedor(preset: str) -> ProveedorOpenAICompatible:
    return ProveedorOpenAICompatible(ConfigModelo(preset, "m", None, "k"), ClienteFalso([]))


# --- Funciones compartidas -----------------------------------------------------------------


def test_los_esquemas_reales_traen_extensiones() -> None:
    # Si el servidor dejara de mandarlas, estos tests ya no probarían nada.
    assert "x-mcp-header" in _claves(ESQUEMAS_GITHUB["list_commits"]["input_schema"])


@pytest.mark.parametrize("nombre", sorted(ESQUEMAS_GITHUB))
def test_quitar_extensiones_en_los_esquemas_reales(nombre: str) -> None:
    original = ESQUEMAS_GITHUB[nombre]["input_schema"]
    limpio = quitar_extensiones(original)
    assert not any(clave.startswith("x-") for clave in _claves(limpio))
    # Lo demás no cambia: mismas propiedades, tipos y obligatorios.
    assert limpio["required"] == original["required"]
    assert limpio["properties"].keys() == original["properties"].keys()
    assert limpio["properties"]["owner"] == {
        k: v for k, v in original["properties"]["owner"].items() if not k.startswith("x-")
    }


def test_quitar_extensiones_no_toca_propiedades_que_empiezan_con_x() -> None:
    esquema = {
        "type": "object",
        "x-interna": True,
        "properties": {"x-total": {"type": "number", "x-mcp-header": "X-Total"}},
    }
    assert quitar_extensiones(esquema) == {
        "type": "object",
        "properties": {"x-total": {"type": "number"}},
    }


def test_resolver_referencias() -> None:
    resuelto = resolver_referencias(CON_REFERENCIAS)
    persona = {"type": "object", "properties": {"login": {"type": "string"}}}
    assert resuelto == {
        "type": "object",
        "properties": {
            "autor": {**persona, "description": "Quién lo escribió"},
            "revisores": {"type": "array", "items": persona},
        },
    }


def test_resolver_referencias_recursivas() -> None:
    esquema = {
        "type": "object",
        "properties": {"raiz": {"$ref": "#/$defs/Nodo"}},
        "$defs": {"Nodo": {"type": "object", "properties": {"hijo": {"$ref": "#/$defs/Nodo"}}}},
    }
    resuelto = resolver_referencias(esquema)
    assert resuelto["properties"]["raiz"]["properties"]["hijo"] == {"type": "object"}
    assert "$ref" not in json.dumps(resuelto)


def test_una_referencia_externa_se_deja() -> None:
    esquema = {"type": "object", "properties": {"a": {"$ref": "https://ejemplo/esquema.json"}}}
    assert resolver_referencias(esquema) == esquema


# --- Adaptación de cada preset con los esquemas reales de GitHub ---------------------------


@pytest.mark.parametrize("preset", sorted(PRESETS))
@pytest.mark.parametrize("nombre", sorted(ESQUEMAS_GITHUB))
def test_cada_preset_quita_las_extensiones(preset: str, nombre: str) -> None:
    original = ESQUEMAS_GITHUB[nombre]["input_schema"]
    adaptado = _proveedor(preset).adaptar_esquema(original)
    assert adaptado == quitar_extensiones(original)
    # El original no se toca: la herramienta lo guarda tal cual lo dio el servidor.
    assert "x-mcp-header" in _claves(original)


def test_gemini_resuelve_las_referencias() -> None:
    adaptado = _proveedor("gemini").adaptar_esquema(CON_REFERENCIAS)
    assert {"$ref", "$defs", "definitions"}.isdisjoint(_claves(adaptado))


@pytest.mark.parametrize("preset", ["groq", "openrouter", "openai", "personalizado"])
def test_los_demas_presets_dejan_las_referencias(preset: str) -> None:
    # Solo se quita lo que se comprobó que una API rechaza.
    assert _proveedor(preset).adaptar_esquema(CON_REFERENCIAS) == CON_REFERENCIAS


def test_al_responder_manda_el_esquema_adaptado() -> None:
    cliente = ClienteFalso([respuesta_openai("ok")])
    proveedor = ProveedorOpenAICompatible(ConfigModelo("groq", "m", None, "k"), cliente)
    esquema = ESQUEMAS_GITHUB["list_commits"]["input_schema"]
    herramienta = Herramienta("github__list_commits", "Lista commits", esquema, lambda: "")

    proveedor.responder([], [herramienta])

    enviado = cliente.pedidos[0]["tools"][0]["function"]["parameters"]
    assert enviado == quitar_extensiones(esquema)
    assert herramienta.parametros is esquema
