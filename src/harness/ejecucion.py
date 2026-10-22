"""Implementación + validación: si los tests fallan, el modelo recibe el error y corrige."""

from collections.abc import Callable
from dataclasses import dataclass

from harness.bucle import Paso, ejecutar_bucle
from harness.prompts import crear_mensaje_validacion_fallida
from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, Mensaje, Rol

# Devuelve (pasó, informe con la salida de tests y lint).
Validador = Callable[[], tuple[bool, str]]


@dataclass
class Validacion:
    """Resultado de una corrida de tests + lint al final de una vuelta."""

    intento: int
    es_valido: bool
    informe: str


@dataclass
class ResultadoEjecucion:
    """Cómo terminó la ejecución completa de un ticket."""

    texto_final: str | None
    pasos: int
    tokens_entrada: int
    tokens_salida: int
    es_limite_alcanzado: bool
    # None si no hubo validación (repo sin harness.yaml).
    es_validado: bool | None
    validaciones: int


def ejecutar_ticket(
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    herramientas: list[Herramienta],
    max_pasos: int,
    validar: Validador | None = None,
    max_intentos_validacion: int = 0,
    al_paso: Callable[[Paso], None] | None = None,
    al_validar: Callable[[Validacion], None] | None = None,
    al_aviso: Callable[[str], None] | None = None,
) -> ResultadoEjecucion:
    """Corre el bucle; cuando el modelo termina, valida y, si falla, le devuelve el error.

    `max_pasos` es el total de todas las vueltas. Se vuelve de la validación a la
    implementación como mucho `max_intentos_validacion` veces.
    """
    resultado = ResultadoEjecucion(None, 0, 0, 0, False, None, 0)
    while True:
        bucle = ejecutar_bucle(
            proveedor, mensajes, herramientas, max_pasos - resultado.pasos, al_paso,
            primer_paso=resultado.pasos + 1, al_aviso=al_aviso,
        )
        _acumular(resultado, bucle.pasos, bucle.tokens_entrada, bucle.tokens_salida)
        resultado.texto_final = bucle.texto_final
        if bucle.es_limite_alcanzado:
            resultado.es_limite_alcanzado = True
            return resultado
        if validar is None:
            return resultado

        es_valido, informe = validar()
        resultado.validaciones += 1
        resultado.es_validado = es_valido
        if al_validar:
            al_validar(Validacion(resultado.validaciones, es_valido, informe))
        vueltas = resultado.validaciones - 1
        if es_valido or vueltas >= max_intentos_validacion:
            return resultado
        if resultado.pasos >= max_pasos:
            resultado.es_limite_alcanzado = True
            return resultado
        texto = crear_mensaje_validacion_fallida(informe, vueltas + 1, max_intentos_validacion)
        mensajes.append(Mensaje(Rol.USUARIO, texto))


def _acumular(resultado: ResultadoEjecucion, pasos: int, entrada: int, salida: int) -> None:
    resultado.pasos += pasos
    resultado.tokens_entrada += entrada
    resultado.tokens_salida += salida
