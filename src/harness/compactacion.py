"""Compactación de la conversación: cuando crece demasiado, se acortan los resultados viejos.

Cada pedido manda la conversación entera, así que una ejecución larga gasta cada vez más tokens
y puede no entrar en un modelo de respaldo con menos contexto. Dos modos
(limites.compactacion en harness.yaml):

- marca (por defecto): los resultados de herramientas más viejos se reemplazan por una marca
  que dice qué eran, y el modelo puede volver a pedirlos. No cuesta pedidos.
- resumen: un pedido aparte al mismo modelo resume esos resultados, y el resumen va en el
  lugar del primero. Cuesta un pedido; si falla o no ahorra, se usa la marca.

Nunca se borra un mensaje, solo se acorta su contenido: así no se rompe la relación entre cada
llamada y su resultado, ni las firmas de Gemini.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from harness.prompts import PROMPT_RESUMEN_COMPACTACION
from harness.proveedores.base import ErrorProveedor, ProveedorLLM
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
MODO_MARCA = "marca"
MODO_RESUMEN = "resumen"
MODOS_COMPACTACION = (MODO_MARCA, MODO_RESUMEN)
# El resumen nunca crece más que esto, aunque el modelo escriba de más.
MAX_CARACTERES_RESUMEN = 2000
# Lo que se manda a resumir de cada resultado: el pedido de resumen también tiene límites.
MAX_CARACTERES_POR_RESULTADO = 4000
INICIO_RESUMEN = "[Resumen de resultados viejos, hecho para ahorrar contexto:"
MARCA_RESUMEN = (
    INICIO_RESUMEN + " {cantidad} resultados. Si necesitas el detalle, vuelve a llamar la "
    "herramienta.]\n{resumen}"
)
MARCA_INCLUIDO = (
    "[Incluido en el resumen de un resultado anterior: {llamada}. Vuelve a llamar la "
    "herramienta si lo necesitas.]"
)


@dataclass(frozen=True)
class Compactacion:
    """Cuántos resultados se omitieron, cuántos caracteres se ahorraron y cómo."""

    resultados_omitidos: int
    caracteres_ahorrados: int
    modo: str = MODO_MARCA
    # Lo que costó el pedido de resumen (0 con la marca).
    tokens_entrada: int = 0
    tokens_salida: int = 0


def tamano(mensajes: list[Mensaje]) -> int:
    """Caracteres de la conversación (textos, resultados y argumentos de las llamadas)."""
    total = 0
    for mensaje in mensajes:
        total += len(mensaje.contenido or "")
        total += sum(len(json.dumps(ll.argumentos, ensure_ascii=False)) for ll in mensaje.llamadas)
    return total


def compactar(
    mensajes: list[Mensaje], max_caracteres: int, resumidor: ProveedorLLM | None = None
) -> Compactacion | None:
    """Si la conversación pasa del máximo, acorta los resultados más viejos (en el lugar).

    Con `resumidor` (modo resumen) se le pide un resumen de esos resultados; si falla, sale
    vacío o no ahorra, se usa la marca.
    """
    elegidos = _elegir(mensajes, max_caracteres)
    if not elegidos:
        return None
    if resumidor is not None:
        compactacion = _compactar_con_resumen(mensajes, elegidos, resumidor)
        if compactacion is not None:
            return compactacion
    return _compactar_con_marca(mensajes, elegidos)


def _elegir(mensajes: list[Mensaje], max_caracteres: int) -> list[int]:
    """Los resultados viejos que hay que acortar para bajar a FRACCION_OBJETIVO del máximo."""
    actual = tamano(mensajes)
    if actual <= max_caracteres:
        return []
    objetivo = int(max_caracteres * FRACCION_OBJETIVO)
    llamadas = _llamadas_por_id(mensajes)
    elegidos, ahorrados = [], 0
    for indice in _candidatos(mensajes):
        if actual - ahorrados <= objetivo:
            break
        mensaje = mensajes[indice]
        marca = _marca(mensaje, llamadas.get(mensaje.id_llamada or ""))
        ahorro = len(mensaje.contenido or "") - len(marca)
        if ahorro > 0:
            elegidos.append(indice)
            ahorrados += ahorro
    return elegidos


def _compactar_con_marca(mensajes: list[Mensaje], elegidos: list[int]) -> Compactacion:
    llamadas = _llamadas_por_id(mensajes)
    ahorrados = 0
    for indice in elegidos:
        mensaje = mensajes[indice]
        marca = _marca(mensaje, llamadas.get(mensaje.id_llamada or ""))
        ahorrados += len(mensaje.contenido or "") - len(marca)
        mensajes[indice] = Mensaje(Rol.HERRAMIENTA, marca, id_llamada=mensaje.id_llamada)
    return Compactacion(len(elegidos), ahorrados)


def _compactar_con_resumen(
    mensajes: list[Mensaje], elegidos: list[int], resumidor: ProveedorLLM
) -> Compactacion | None:
    """El resumen en el primer resultado elegido y una marca corta en los demás."""
    llamadas = _llamadas_por_id(mensajes)
    descripciones = [_describir(llamadas.get(mensajes[i].id_llamada or "")) for i in elegidos]
    texto = "\n\n".join(
        f"### {descripcion}\n{(mensajes[i].contenido or '')[:MAX_CARACTERES_POR_RESULTADO]}"
        for descripcion, i in zip(descripciones, elegidos)
    )
    pedido = [Mensaje(Rol.SISTEMA, PROMPT_RESUMEN_COMPACTACION), Mensaje(Rol.USUARIO, texto)]
    try:
        respuesta = resumidor.responder(pedido, [])
    except ErrorProveedor:
        return None
    resumen = (respuesta.texto or "").strip()[:MAX_CARACTERES_RESUMEN]
    if not resumen:
        return None
    nuevos = [MARCA_RESUMEN.format(cantidad=len(elegidos), resumen=resumen)]
    nuevos += [MARCA_INCLUIDO.format(llamada=d) for d in descripciones[1:]]
    ahorrados = sum(len(mensajes[i].contenido or "") for i in elegidos) - sum(map(len, nuevos))
    if ahorrados <= 0:
        return None
    for indice, contenido in zip(elegidos, nuevos):
        id_llamada = mensajes[indice].id_llamada
        mensajes[indice] = Mensaje(Rol.HERRAMIENTA, contenido, id_llamada=id_llamada)
    return Compactacion(
        len(elegidos), ahorrados, MODO_RESUMEN, respuesta.tokens_entrada, respuesta.tokens_salida
    )


class ProveedorQueCompacta:
    """Cumple ProveedorLLM: compacta la conversación (la misma lista del bucle) antes de pedir.

    Como compacta la lista en el lugar, la sesión de harness resume guarda lo compactado.
    """

    def __init__(
        self,
        proveedor: ProveedorLLM,
        max_caracteres: int,
        al_compactar: Callable[[Compactacion], None] | None = None,
        modo: str = MODO_MARCA,
    ) -> None:
        self._proveedor = proveedor
        self._max_caracteres = max_caracteres
        self._al_compactar = al_compactar
        self._modo = modo

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[Herramienta],
        herramienta_obligatoria: str | None = None,
    ) -> RespuestaModelo:
        resumidor = self._proveedor if self._modo == MODO_RESUMEN else None
        compactacion = compactar(mensajes, self._max_caracteres, resumidor)
        if compactacion and self._al_compactar:
            self._al_compactar(compactacion)
        respuesta = self._proveedor.responder(mensajes, herramientas, herramienta_obligatoria)
        if compactacion and compactacion.modo == MODO_RESUMEN:
            # El pedido de resumen también se paga: se suma a esta respuesta para los totales.
            respuesta.tokens_entrada += compactacion.tokens_entrada
            respuesta.tokens_salida += compactacion.tokens_salida
        return respuesta

    def adaptar_esquema(self, esquema: dict[str, Any]) -> dict[str, Any]:
        return self._proveedor.adaptar_esquema(esquema)


def _candidatos(mensajes: list[Mensaje]) -> list[int]:
    """Resultados de herramientas fuera de los últimos pasos y sin compactar, del más viejo."""
    pasos = [i for i, m in enumerate(mensajes) if m.rol == Rol.ASISTENTE and m.llamadas]
    limite = pasos[-PASOS_RECIENTES] if len(pasos) >= PASOS_RECIENTES else 0
    return [
        i for i, m in enumerate(mensajes[:limite])
        if m.rol == Rol.HERRAMIENTA and not _esta_compactado(m.contenido or "")
    ]


def _esta_compactado(contenido: str) -> bool:
    return contenido.startswith((INICIO_MARCA, INICIO_RESUMEN, MARCA_INCLUIDO.split("{")[0]))


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
