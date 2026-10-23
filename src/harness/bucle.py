"""Bucle de tool calling: el modelo pide herramientas y el harness las ejecuta."""

from collections.abc import Callable
from dataclasses import dataclass

from harness.prompts import MENSAJE_RESPUESTA_VACIA
from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, Rol

MAX_PASOS_POR_DEFECTO = 30
# Respuestas vacías seguidas que se toleran; la siguiente corta la ejecución.
MAX_RESPUESTAS_VACIAS = 2


class ErrorRespuestaVacia(Exception):
    """El modelo respondió sin texto ni herramientas demasiadas veces seguidas."""


@dataclass
class Paso:
    """Una herramienta ejecutada dentro de un paso del bucle."""

    numero: int
    llamada: LlamadaHerramienta
    resultado: str
    es_error: bool


@dataclass
class ResultadoBucle:
    """Cómo terminó el bucle."""

    texto_final: str | None
    pasos: int
    tokens_entrada: int
    tokens_salida: int
    es_limite_alcanzado: bool


def ejecutar_bucle(
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    herramientas: list[Herramienta],
    max_pasos: int = MAX_PASOS_POR_DEFECTO,
    al_paso: Callable[[Paso], None] | None = None,
    primer_paso: int = 1,
    al_aviso: Callable[[str], None] | None = None,
    es_fin: Callable[[], bool] | None = None,
) -> ResultadoBucle:
    """Llama al modelo hasta que deje de pedir herramientas o se acaben los pasos.

    Agrega a `mensajes` cada respuesta y cada resultado, para poder registrarlos o retomarlos.
    `primer_paso` numera los pasos cuando el bucle continúa después de una validación.
    Una respuesta vacía no cuenta como final: se le pide al modelo que continúe.
    `es_fin` corta el bucle después de un paso aunque el modelo haya pedido herramientas
    (la ronda de plan termina cuando el modelo llama a proponer_plan).
    """
    por_nombre = {h.nombre: h for h in herramientas}
    tokens_entrada = tokens_salida = 0
    vacias_seguidas = 0
    for numero in range(primer_paso, primer_paso + max_pasos):
        respuesta = proveedor.responder(mensajes, herramientas)
        tokens_entrada += respuesta.tokens_entrada
        tokens_salida += respuesta.tokens_salida
        if _es_vacia(respuesta.texto, respuesta.llamadas):
            vacias_seguidas += 1
            _insistir(mensajes, vacias_seguidas, al_aviso)
            continue
        vacias_seguidas = 0
        mensajes.append(Mensaje(Rol.ASISTENTE, respuesta.texto, respuesta.llamadas))
        if not respuesta.llamadas:
            pasos = numero - primer_paso + 1
            return ResultadoBucle(respuesta.texto, pasos, tokens_entrada, tokens_salida, False)
        for llamada in respuesta.llamadas:
            resultado, es_error = ejecutar_llamada(llamada, por_nombre)
            mensajes.append(Mensaje(Rol.HERRAMIENTA, resultado, id_llamada=llamada.id))
            if al_paso:
                al_paso(Paso(numero, llamada, resultado, es_error))
        if es_fin and es_fin():
            pasos = numero - primer_paso + 1
            return ResultadoBucle(None, pasos, tokens_entrada, tokens_salida, False)
    return ResultadoBucle(None, max_pasos, tokens_entrada, tokens_salida, True)


def _es_vacia(texto: str | None, llamadas: list[LlamadaHerramienta]) -> bool:
    return not llamadas and not (texto or "").strip()


def _insistir(
    mensajes: list[Mensaje], vacias_seguidas: int, al_aviso: Callable[[str], None] | None
) -> None:
    """Le pide al modelo que continúe, o corta si ya respondió vacío demasiadas veces."""
    if vacias_seguidas > MAX_RESPUESTAS_VACIAS:
        raise ErrorRespuestaVacia(
            f"El modelo respondió vacío (sin texto ni herramientas) {vacias_seguidas} veces "
            "seguidas: se corta la ejecución."
        )
    if al_aviso:
        al_aviso(
            f"El modelo respondió vacío: se le pide que continúe "
            f"({vacias_seguidas}/{MAX_RESPUESTAS_VACIAS})."
        )
    # La respuesta vacía no se guarda: algunos proveedores rechazan un mensaje de asistente
    # sin contenido ni llamadas.
    mensajes.append(Mensaje(Rol.USUARIO, MENSAJE_RESPUESTA_VACIA))


def ejecutar_llamada(
    llamada: LlamadaHerramienta, por_nombre: dict[str, Herramienta]
) -> tuple[str, bool]:
    """Ejecuta una llamada y devuelve (resultado, es_error); nunca lanza excepciones."""
    if llamada.error_argumentos:
        return f"Error: {llamada.error_argumentos} Vuelve a llamar con argumentos válidos.", True
    herramienta = por_nombre.get(llamada.nombre)
    if herramienta is None:
        disponibles = ", ".join(por_nombre)
        return (
            f"Error: la herramienta '{llamada.nombre}' no existe. Disponibles: {disponibles}.",
            True,
        )
    try:
        return herramienta.funcion(**llamada.argumentos), False
    except TypeError as error:
        return f"Error: argumentos inválidos para '{llamada.nombre}': {error}", True
    # Se captura todo a propósito: cualquier fallo de una herramienta se le devuelve al
    # modelo para que lo corrija, en vez de cortar la ejecución.
    except Exception as error:
        return f"Error: {error}", True
