"""Ronda de planificación: el modelo explora con herramientas de lectura y llama a proponer_plan.

En esta ronda no puede escribir ni ejecutar comandos, así que planificar no cambia nada.
"""

from collections.abc import Callable
from dataclasses import dataclass

from harness.bucle import Paso, ejecutar_bucle
from harness.plan import NOMBRE_HERRAMIENTA_PLAN, Plan, crear_herramienta_plan
from harness.prompts import (
    MENSAJE_ENTREGAR_PLAN,
    MENSAJE_ULTIMA_OPORTUNIDAD_PLAN,
    crear_aviso_pasos_plan,
    crear_mensaje_correccion_plan,
    crear_mensaje_planificar,
)
from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, Mensaje, Rol

MAX_PASOS_PLAN_POR_DEFECTO = 10
# Después de explorar, el plan se pide con solo proponer_plan (y tool_choice donde se puede):
# un pedido, y una última oportunidad con un mensaje explícito si no llega un plan válido.
PEDIDOS_ENTREGA_PLAN = (MENSAJE_ENTREGAR_PLAN, MENSAJE_ULTIMA_OPORTUNIDAD_PLAN)
# Con esta cantidad de pasos restantes (o menos) se le recuerda al modelo que proponga el plan.
PASOS_RESTANTES_PARA_AVISAR = 3
AVISO_FIN_EXPLORACION = "Terminaron los pasos de exploración: se le pide el plan al modelo."
AVISO_ULTIMA_OPORTUNIDAD = "El modelo no propuso un plan válido: se le da una última oportunidad."


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
    max_pasos_plan: int = MAX_PASOS_PLAN_POR_DEFECTO,
    al_paso: Callable[[Paso], None] | None = None,
    al_aviso: Callable[[str], None] | None = None,
    primer_paso: int = 1,
    correccion: str | None = None,
    al_respuesta: Callable[[int, str], None] | None = None,
) -> ResultadoPlan:
    """Corre una ronda de plan; termina en cuanto el modelo llama a proponer_plan.

    Explora hasta `max_pasos_plan` pasos, su propio presupuesto (no descuenta de max_pasos).
    Con `correccion` es una ronda más después de que el usuario pidió un cambio al plan.
    """
    propuestos: list[Plan] = []
    herramienta_plan = crear_herramienta_plan(propuestos.append)
    pedido_inicial = (
        crear_mensaje_correccion_plan(correccion, max_pasos_plan)
        if correccion is not None else crear_mensaje_planificar(max_pasos_plan)
    )
    mensajes.append(Mensaje(Rol.USUARIO, pedido_inicial))
    bucle = ejecutar_bucle(
        proveedor, mensajes, [*herramientas_lectura, herramienta_plan],
        max_pasos_plan, al_paso, primer_paso=primer_paso, al_aviso=al_aviso,
        es_fin=lambda: bool(propuestos), nota_restantes=_nota_restantes,
        al_respuesta=al_respuesta,
    )
    pasos, entrada, salida = bucle.pasos, bucle.tokens_entrada, bucle.tokens_salida
    # Respondió con texto o se le acabaron los pasos: se le pide el plan, sin explorar más.
    for intento, pedido in enumerate(PEDIDOS_ENTREGA_PLAN):
        if propuestos:
            break
        if al_aviso:
            al_aviso(AVISO_ULTIMA_OPORTUNIDAD if intento else AVISO_FIN_EXPLORACION)
        mensajes.append(Mensaje(Rol.USUARIO, pedido))
        # Un modelo puede seguir llamando herramientas que ya no tiene (pasó con leer_archivo
        # y read_file): tool_choice lo obliga a proponer_plan donde el proveedor lo acepta.
        extra = ejecutar_bucle(
            proveedor, mensajes, [herramienta_plan], 1, al_paso,
            primer_paso=primer_paso + pasos, al_aviso=al_aviso, es_fin=lambda: bool(propuestos),
            herramienta_obligatoria=NOMBRE_HERRAMIENTA_PLAN, al_respuesta=al_respuesta,
        )
        pasos += extra.pasos
        entrada += extra.tokens_entrada
        salida += extra.tokens_salida
    if not propuestos:
        raise ErrorPlan("El modelo no propuso un plan válido: se corta antes de implementar.")
    return ResultadoPlan(propuestos[-1], pasos, entrada, salida)


def _nota_restantes(restantes: int) -> str | None:
    # Con 0 no hace falta: enseguida llega el pedido final de proponer_plan.
    if 0 < restantes <= PASOS_RESTANTES_PARA_AVISAR:
        return crear_aviso_pasos_plan(restantes)
    return None

