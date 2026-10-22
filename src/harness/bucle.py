"""Bucle de tool calling: el modelo pide herramientas y el harness las ejecuta."""

from collections.abc import Callable
from dataclasses import dataclass

from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, Rol

MAX_PASOS_POR_DEFECTO = 30


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
) -> ResultadoBucle:
    """Llama al modelo hasta que deje de pedir herramientas o se acaben los pasos.

    Agrega a `mensajes` cada respuesta y cada resultado, para poder registrarlos o retomarlos.
    """
    por_nombre = {h.nombre: h for h in herramientas}
    tokens_entrada = tokens_salida = 0
    for numero in range(1, max_pasos + 1):
        respuesta = proveedor.responder(mensajes, herramientas)
        tokens_entrada += respuesta.tokens_entrada
        tokens_salida += respuesta.tokens_salida
        mensajes.append(Mensaje(Rol.ASISTENTE, respuesta.texto, respuesta.llamadas))
        if not respuesta.llamadas:
            return ResultadoBucle(respuesta.texto, numero, tokens_entrada, tokens_salida, False)
        for llamada in respuesta.llamadas:
            resultado, es_error = ejecutar_llamada(llamada, por_nombre)
            mensajes.append(Mensaje(Rol.HERRAMIENTA, resultado, id_llamada=llamada.id))
            if al_paso:
                al_paso(Paso(numero, llamada, resultado, es_error))
    return ResultadoBucle(None, max_pasos, tokens_entrada, tokens_salida, True)


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
