"""Ronda de planificación: el modelo explora con herramientas de lectura y llama a proponer_plan.

En esta ronda no puede escribir ni ejecutar comandos, así que planificar no cambia nada.
"""

from collections.abc import Callable
from dataclasses import dataclass

from harness.bucle import Paso, ResultadoBucle, ejecutar_bucle
from harness.plan import Plan, crear_herramienta_plan
from harness.prompts import MENSAJE_ENTREGAR_PLAN, MENSAJE_PLANIFICAR
from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, Mensaje, Rol

MAX_PASOS_PLAN = 10
# Pasos para el pedido final "llama a proponer_plan ahora", con solo esa herramienta: uno, y
# otro por si el primer intento trae argumentos inválidos.
MAX_PASOS_ENTREGA_PLAN = 2


class ErrorPlan(Exception):
    """El modelo no propuso un plan válido."""


@dataclass
class ResultadoPlan:
    """Plan propuesto y lo que costó obtenerlo."""

    plan: Plan
    pasos: int
    tokens_entrada: int
    tokens_salida: int


def planificar(
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    herramientas_lectura: list[Herramienta],
    max_pasos: int,
    al_paso: Callable[[Paso], None] | None = None,
    al_aviso: Callable[[str], None] | None = None,
) -> ResultadoPlan:
    """Corre la ronda de plan; termina en cuanto el modelo llama a proponer_plan."""
    propuestos: list[Plan] = []
    herramienta_plan = crear_herramienta_plan(propuestos.append)
    mensajes.append(Mensaje(Rol.USUARIO, MENSAJE_PLANIFICAR))
    bucle = ejecutar_bucle(
        proveedor, mensajes, [*herramientas_lectura, herramienta_plan],
        min(MAX_PASOS_PLAN, max_pasos), al_paso, al_aviso=al_aviso,
        es_fin=lambda: bool(propuestos),
    )
    pasos, entrada, salida = bucle.pasos, bucle.tokens_entrada, bucle.tokens_salida
    if not propuestos and pasos < max_pasos:
        # Respondió con texto o se le acabaron los pasos: se le pide el plan, sin explorar más.
        if al_aviso:
            al_aviso("El modelo no propuso un plan: se le pide que llame a proponer_plan.")
        mensajes.append(Mensaje(Rol.USUARIO, MENSAJE_ENTREGAR_PLAN))
        extra = ejecutar_bucle(
            proveedor, mensajes, [herramienta_plan],
            min(MAX_PASOS_ENTREGA_PLAN, max_pasos - pasos), al_paso,
            primer_paso=pasos + 1, al_aviso=al_aviso, es_fin=lambda: bool(propuestos),
        )
        pasos, entrada, salida = _sumar(bucle, extra)
    if not propuestos:
        raise ErrorPlan("El modelo no propuso un plan válido: se corta antes de implementar.")
    return ResultadoPlan(propuestos[-1], pasos, entrada, salida)


def _sumar(primero: ResultadoBucle, segundo: ResultadoBucle) -> tuple[int, int, int]:
    return (
        primero.pasos + segundo.pasos,
        primero.tokens_entrada + segundo.tokens_entrada,
        primero.tokens_salida + segundo.tokens_salida,
    )
