"""Herramientas de un servidor MCP convertidas en Herramienta del harness.

El bucle y los proveedores no saben que existe MCP: ven una Herramienta más, con el nombre
prefijado por el servidor y el esquema tal cual lo dio (cada proveedor lo adapta al mandarlo).
"""

import json
from collections.abc import Sequence
from typing import Any

from harness.cliente_mcp import HerramientaMCP, SesionMCP
from harness.herramientas.archivos import ErrorHerramienta
from harness.tipos import Herramienta
from harness.variables import ocultar_secretos

SEPARADOR_PREFIJO = "__"
PREFIJO_GITHUB = f"github{SEPARADOR_PREFIJO}"
# Solo lectura, y pocas: cada herramienta suma tokens a todos los pedidos, con cualquier
# modelo. Las de escritura (crear el PR, comentar) las llama el harness, nunca el modelo.
# search_code, get_commit y list_branches se pueden sumar si hacen falta.
HERRAMIENTAS_LECTURA_GITHUB = (
    "get_file_contents",
    "list_commits",
    "search_pull_requests",
    "pull_request_read",
)
# GitHub responde casi todo en JSON de una línea: con sangría tiene líneas y el recorte las
# deja completas. 1 espacio alcanza para leerlo y gasta menos que 2 o 4.
SANGRIA_JSON = 1
PISTA_RECORTE = "Si necesitas más, pide menos resultados (perPage) o un archivo concreto."


def crear_herramientas_mcp(
    sesion: SesionMCP,
    prefijo: str,
    permitidas: Sequence[str],
    secretos: Sequence[str],
    max_caracteres: int,
) -> list[Herramienta]:
    """Las herramientas permitidas del servidor, en el orden de la lista permitida."""
    por_nombre = {h.nombre: h for h in sesion.herramientas}
    return [
        _convertir(sesion, por_nombre[nombre], prefijo, secretos, max_caracteres)
        for nombre in permitidas
        if nombre in por_nombre
    ]


def es_herramienta_github(herramienta: Herramienta) -> bool:
    """True si la herramienta viene del servidor MCP de GitHub."""
    return herramienta.nombre.startswith(PREFIJO_GITHUB)


def preparar_salida(texto: str, secretos: Sequence[str], max_caracteres: int) -> str:
    """Salida de una herramienta MCP lista para el modelo: sin secretos, legible y sin pasarse.

    Se oculta antes de recortar: el recorte podría dejar un secreto partido a la mitad.
    """
    texto = ocultar_secretos(_formatear_json(ocultar_secretos(texto, secretos)), secretos)
    if len(texto) <= max_caracteres:
        return texto
    return _recortar_principio(texto, max_caracteres)


def _formatear_json(texto: str) -> str:
    try:
        datos = json.loads(texto)
    except ValueError:
        return texto
    return json.dumps(datos, indent=SANGRIA_JSON, ensure_ascii=False)


def _recortar_principio(texto: str, max_caracteres: int) -> str:
    # A diferencia de la salida de los tests (donde importa el final), aquí lo que tiene sentido
    # es el principio: un JSON sin su comienzo o un archivo desde la mitad no sirven.
    marca = f"\n[... {len(texto)} caracteres en total, se omitió el resto. {PISTA_RECORTE}]"
    principio = texto[: max(max_caracteres - len(marca), 0)]
    # Con varias líneas, se corta en la última completa; con una sola, donde llegue.
    if "\n" in principio:
        principio = principio[: principio.rindex("\n")]
    return principio + marca


def _convertir(
    sesion: SesionMCP,
    herramienta: HerramientaMCP,
    prefijo: str,
    secretos: Sequence[str],
    max_caracteres: int,
) -> Herramienta:
    def llamar(**argumentos: Any) -> str:
        resultado = sesion.llamar(herramienta.nombre, argumentos)
        texto = preparar_salida(resultado.texto, secretos, max_caracteres)
        if resultado.es_error:
            # El texto es el motivo (por ejemplo "repo no encontrado"): el modelo lo necesita.
            raise ErrorHerramienta(texto)
        return texto

    return Herramienta(
        nombre=f"{prefijo}{herramienta.nombre}",
        descripcion=herramienta.descripcion,
        parametros=herramienta.esquema,
        funcion=llamar,
    )
