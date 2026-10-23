"""Ronda de planificación: el modelo explora con herramientas de lectura y llama a proponer_plan.

En esta ronda no puede escribir ni ejecutar comandos, así que planificar no cambia nada.
"""

from collections.abc import Callable
from dataclasses import dataclass

from harness.bucle import Paso, ResultadoBucle, ejecutar_bucle
from harness.plan import Plan, crear_herramienta_plan
from harness.prompts import (
    MENSAJE_ENTREGAR_PLAN,
    crear_aviso_pasos_plan,
    crear_mensaje_planificar,
)
from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, Mensaje, Rol

MAX_PASOS_PLAN_POR_DEFECTO = 10
# Pasos para el pedido final "llama a proponer_plan ahora", con solo esa herramienta: uno, y
# otro por si el primer intento trae argumentos inválidos.
MAX_PASOS_ENTREGA_PLAN = 2
# Con esta cantidad de pasos restantes (o menos) se le recuerda al modelo que proponga el plan.
PASOS_RESTANTES_PARA_AVISAR = 3
AVISO_FIN_EXPLORACION = "Terminaron los pasos de exploración: se le pide el plan al modelo."


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
    max_pasos_plan: int | None = None,
) -> ResultadoPlan:
    """Corre la ronda de plan; termina en cuanto el modelo llama a proponer_plan.

    Explora hasta `calcular_pasos_plan(max_pasos, max_pasos_plan)` pasos.
    """
    propuestos: list[Plan] = []
    herramienta_plan = crear_herramienta_plan(propuestos.append)
    pasos_exploracion = calcular_pasos_plan(max_pasos, max_pasos_plan)
    mensajes.append(Mensaje(Rol.USUARIO, crear_mensaje_planificar(pasos_exploracion)))
    bucle = ejecutar_bucle(
        proveedor, mensajes, [*herramientas_lectura, herramienta_plan],
        pasos_exploracion, al_paso, al_aviso=al_aviso,
        es_fin=lambda: bool(propuestos), nota_restantes=_nota_restantes,
    )
    pasos, entrada, salida = bucle.pasos, bucle.tokens_entrada, bucle.tokens_salida
    if not propuestos and pasos < max_pasos:
        # Respondió con texto o se le acabaron los pasos: se le pide el plan, sin explorar más.
        if al_aviso:
            al_aviso(AVISO_FIN_EXPLORACION)
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


def calcular_pasos_plan(max_pasos: int, max_pasos_plan: int | None) -> int:
    """Pasos para explorar: el de harness.yaml o, sin la clave, min(10, mitad del total)."""
    if max_pasos_plan is None:
        # La mitad como tope: siempre queda al menos la otra mitad para implementar.
        return min(MAX_PASOS_PLAN_POR_DEFECTO, max_pasos // 2)
    # --max-pasos puede bajar el total por debajo de lo que dice harness.yaml.
    return min(max_pasos_plan, max_pasos)


def _nota_restantes(restantes: int) -> str | None:
    # Con 0 no hace falta: enseguida llega el pedido final de proponer_plan.
    if 0 < restantes <= PASOS_RESTANTES_PARA_AVISAR:
        return crear_aviso_pasos_plan(restantes)
    return None


def _sumar(primero: ResultadoBucle, segundo: ResultadoBucle) -> tuple[int, int, int]:
    return (
        primero.pasos + segundo.pasos,
        primero.tokens_entrada + segundo.tokens_entrada,
        primero.tokens_salida + segundo.tokens_salida,
    )
