"""Compactación de la conversación: cuando crece demasiado, se acortan los resultados viejos.

Cada pedido manda la conversación entera, así que una ejecución larga gasta cada vez más tokens
y puede no entrar en un modelo de respaldo con menos contexto. No se le pide un resumen al
modelo (costaría un pedido y podría perder información): los resultados de herramientas más
viejos se reemplazan por una marca que dice qué eran, y el modelo puede volver a pedirlos.
Nunca se borra un mensaje, solo se acorta su contenido: así no se rompe la relación entre cada
llamada y su resultado, ni las firmas de Gemini.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, RespuestaModelo, Rol

# Los resultados de los últimos pasos siempre quedan enteros: son con los que trabaja el modelo.
PASOS_RECIENTES = 6
# Al compactar se baja hasta esta fracción del máximo, para no compactar en cada pedido.
FRACCION_OBJETIVO = 0.7
INICIO_MARCA = "[Resultado omitido para ahorrar contexto:"
MARCA_OMITIDO = (
    INICIO_MARCA + " {llamada}, {lineas} líneas. Vuelve a llamar la herramienta si lo necesitas.]"
)
MAX_CARACTERES_ARGUMENTO = 60


@dataclass(frozen=True)
class Compactacion:
    """Cuántos resultados se omitieron y cuántos caracteres se ahorraron."""

    resultados_omitidos: int
    caracteres_ahorrados: int


def tamano(mensajes: list[Mensaje]) -> int:
    """Caracteres de la conversación (textos, resultados y argumentos de las llamadas)."""
    total = 0
    for mensaje in mensajes:
        total += len(mensaje.contenido or "")
        total += sum(len(json.dumps(ll.argumentos, ensure_ascii=False)) for ll in mensaje.llamadas)
    return total


def compactar(mensajes: list[Mensaje], max_caracteres: int) -> Compactacion | None:
    """Si la conversación pasa del máximo, acorta los resultados más viejos (en el lugar)."""
    actual = tamano(mensajes)
    if actual <= max_caracteres:
        return None
    objetivo = int(max_caracteres * FRACCION_OBJETIVO)
    llamadas = _llamadas_por_id(mensajes)
    omitidos = ahorrados = 0
    for indice in _candidatos(mensajes):
        if actual - ahorrados <= objetivo:
            break
        mensaje = mensajes[indice]
        marca = _marca(mensaje, llamadas.get(mensaje.id_llamada or ""))
        ahorro = len(mensaje.contenido or "") - len(marca)
        if ahorro <= 0:
            continue
        mensajes[indice] = Mensaje(Rol.HERRAMIENTA, marca, id_llamada=mensaje.id_llamada)
        omitidos += 1
        ahorrados += ahorro
    return Compactacion(omitidos, ahorrados) if omitidos else None


class ProveedorQueCompacta:
    """Cumple ProveedorLLM: compacta la conversación (la misma lista del bucle) antes de pedir.

    Como compacta la lista en el lugar, la sesión de harness resume guarda lo compactado.
    """

    def __init__(
        self,
        proveedor: ProveedorLLM,
        max_caracteres: int,
        al_compactar: Callable[[Compactacion], None] | None = None,
    ) -> None:
        self._proveedor = proveedor
        self._max_caracteres = max_caracteres
        self._al_compactar = al_compactar

    def responder(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> RespuestaModelo:
        compactacion = compactar(mensajes, self._max_caracteres)
        if compactacion and self._al_compactar:
            self._al_compactar(compactacion)
        return self._proveedor.responder(mensajes, herramientas)

    def adaptar_esquema(self, esquema: dict[str, Any]) -> dict[str, Any]:
        return self._proveedor.adaptar_esquema(esquema)


def _candidatos(mensajes: list[Mensaje]) -> list[int]:
    """Resultados de herramientas fuera de los últimos pasos y sin compactar, del más viejo."""
    pasos = [i for i, m in enumerate(mensajes) if m.rol == Rol.ASISTENTE and m.llamadas]
    limite = pasos[-PASOS_RECIENTES] if len(pasos) >= PASOS_RECIENTES else 0
    return [
        i for i, m in enumerate(mensajes[:limite])
        if m.rol == Rol.HERRAMIENTA and not (m.contenido or "").startswith(INICIO_MARCA)
    ]


def _llamadas_por_id(mensajes: list[Mensaje]) -> dict[str, LlamadaHerramienta]:
    return {ll.id: ll for m in mensajes for ll in m.llamadas}


def _marca(mensaje: Mensaje, llamada: LlamadaHerramienta | None) -> str:
    lineas = len((mensaje.contenido or "").splitlines())
    return MARCA_OMITIDO.format(llamada=_describir(llamada), lineas=lineas)


def _describir(llamada: LlamadaHerramienta | None) -> str:
    """"leer_archivo('src/x.py')": la herramienta y su primer argumento corto."""
    if llamada is None:
        return "una herramienta"
    textos = [v for v in llamada.argumentos.values() if isinstance(v, str)]
    if textos and len(textos[0]) <= MAX_CARACTERES_ARGUMENTO and "\n" not in textos[0]:
        return f"{llamada.nombre}({textos[0]!r})"
    return llamada.nombre
