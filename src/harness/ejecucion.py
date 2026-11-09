"""Implementación + validación: si los tests fallan, el modelo recibe el error y corrige."""

from collections.abc import Callable
from dataclasses import dataclass, field

from harness.alcance import Alcance, EstadoAlcance, resolver_fuera_del_plan
from harness.bucle import Paso, ejecutar_bucle
from harness.bucles import normalizar_salida
from harness.plan import Plan
from harness.planificacion import MAX_PASOS_PLAN_POR_DEFECTO, planificar
from harness.prompts import (
    MENSAJE_IMPLEMENTAR,
    crear_mensaje_revision_con_problemas,
    crear_mensaje_validacion_fallida,
)
from harness.proveedores.base import ProveedorLLM
from harness.resumen_tests import resumir_tests, seccion_tests
from harness.revision import EstadoRevision, Revision, revisar
from harness.tipos import Herramienta, Mensaje, Rol

# Devuelve (pasó, informe con la salida de tests y lint).
Validador = Callable[[], tuple[bool, str]]


@dataclass
class Validacion:
    """Resultado de una corrida de tests + lint al final de una vuelta."""

    intento: int
    es_valido: bool
    informe: str
    # Cuántos tests pasaron ("5 passed in 0.80s"), si la salida lo dice.
    resumen_tests: str | None = None


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
    # None si se corrió sin planificación (--sin-plan).
    plan: Plan | None = None
    es_plan_rechazado: bool = False
    # De la última validación que pasó: va en la descripción del PR.
    resumen_tests: str | None = None
    revision: EstadoRevision = field(default_factory=EstadoRevision)
    # Lo que el usuario pidió cambiar del plan con --aprobar-plan, en orden.
    correcciones_plan: list[str] = field(default_factory=list)
    alcance: EstadoAlcance = field(default_factory=EstadoAlcance)
    # Pasos que no descuentan de max_pasos (plan, revisión, fuera del plan): están en `pasos`,
    # pero la implementación tiene max_pasos - (pasos - pasos_extra).
    pasos_extra: int = 0

    @property
    def pasos_con_presupuesto(self) -> int:
        """Los pasos que cuentan contra max_pasos."""
        return self.pasos - self.pasos_extra


@dataclass
class Planificacion:
    """Cómo se hace la ronda de plan antes de implementar."""

    herramientas_lectura: list[Herramienta]
    # Recibe el plan y los tokens gastados hasta ahí (para mostrarlos con --aprobar-plan).
    # True: se implementa; False: no; un texto: es una corrección y se planifica de nuevo.
    al_planear: Callable[[Plan, tuple[int, int]], bool | str] | None = None
    # Pasos de cada ronda de plan: su propio presupuesto, no descuentan de max_pasos.
    max_pasos_plan: int = MAX_PASOS_PLAN_POR_DEFECTO


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
    planificacion: Planificacion | None = None,
    continuacion: ResultadoEjecucion | None = None,
    revisar_paso: Callable[[Paso], str | None] | None = None,
    revision: Revision | None = None,
    alcance: Alcance | None = None,
) -> ResultadoEjecucion:
    """Planifica (si se pide), implementa y valida; si la validación falla, el modelo corrige.

    `max_pasos` es el total de las vueltas de implementación: cuenta desde que se aprueba el
    plan (el plan tiene su propio presupuesto). Se vuelve de la validación a la implementación
    como mucho `max_intentos_validacion` veces.
    `continuacion` es lo que ya se hizo antes de un corte (harness resume): los pasos, los
    tokens y las validaciones siguen contando desde ahí, y el plan ya está aprobado.
    `revisar_paso` mira cada paso de la implementación (detección de bucles).
    `revision`: con los tests en verde, el modelo revisa su diff; si encuentra problemas,
    vuelve a implementar (hasta revision.max_vueltas revisiones).
    `alcance`: antes de validar, el modelo justifica o revierte lo que cambió fuera del plan.
    """
    resultado = continuacion or ResultadoEjecucion(None, 0, 0, 0, False, None, 0)
    if continuacion is None and planificacion and not _planificar(
        resultado, proveedor, mensajes, planificacion, al_paso, al_aviso
    ):
        return resultado
    informe_anterior = None
    while True:
        bucle = ejecutar_bucle(
            proveedor, mensajes, herramientas, max_pasos - resultado.pasos_con_presupuesto,
            al_paso, primer_paso=resultado.pasos + 1, al_aviso=al_aviso,
            revisar_paso=revisar_paso,
        )
        _acumular(resultado, bucle.pasos, bucle.tokens_entrada, bucle.tokens_salida)
        resultado.texto_final = bucle.texto_final
        if bucle.es_limite_alcanzado:
            resultado.es_limite_alcanzado = True
            return resultado
        if validar is None:
            return resultado
        if alcance and resultado.plan:
            _resolver_alcance(resultado, proveedor, mensajes, alcance, al_paso, al_aviso)

        es_valido, informe = validar()
        resultado.validaciones += 1
        resultado.es_validado = es_valido
        resumen = resumir_tests(seccion_tests(informe)) if es_valido else None
        if es_valido:
            resultado.resumen_tests = resumen
        if al_validar:
            al_validar(Validacion(resultado.validaciones, es_valido, informe, resumen))
        if es_valido:
            if not _hay_que_corregir_por_revision(
                resultado, proveedor, mensajes, revision, max_pasos, al_paso, al_aviso
            ):
                return resultado
            continue
        # Las validaciones que siguieron a una corrección de la revisión no son reintentos.
        vueltas = resultado.validaciones - 1 - resultado.revision.correcciones
        if vueltas >= max_intentos_validacion:
            return resultado
        if resultado.pasos_con_presupuesto >= max_pasos:
            resultado.es_limite_alcanzado = True
            return resultado
        es_repetido = normalizar_salida(informe) == informe_anterior
        informe_anterior = normalizar_salida(informe)
        texto = crear_mensaje_validacion_fallida(
            informe, vueltas + 1, max_intentos_validacion, es_repetido
        )
        mensajes.append(Mensaje(Rol.USUARIO, texto))


def _planificar(
    resultado: ResultadoEjecucion,
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    planificacion: Planificacion,
    al_paso: Callable[[Paso], None] | None,
    al_aviso: Callable[[str], None] | None,
) -> bool:
    """Corre la ronda de plan (y una más por cada corrección del usuario); devuelve si hay
    que seguir con la implementación."""
    correccion = None
    while True:
        ronda = planificar(
            proveedor, mensajes, planificacion.herramientas_lectura,
            planificacion.max_pasos_plan, al_paso, al_aviso, resultado.pasos + 1, correccion,
        )
        _acumular(resultado, ronda.pasos, ronda.tokens_entrada, ronda.tokens_salida)
        # El plan tiene su propio presupuesto: max_pasos cuenta desde que se aprueba.
        resultado.pasos_extra += ronda.pasos
        resultado.plan = ronda.plan
        respuesta = (
            planificacion.al_planear(
                ronda.plan, (resultado.tokens_entrada, resultado.tokens_salida)
            )
            if planificacion.al_planear else True
        )
        if respuesta is True:
            break
        if respuesta is False:
            resultado.es_plan_rechazado = True
            return False
        correccion = respuesta
        resultado.correcciones_plan.append(correccion)
    mensajes.append(Mensaje(Rol.USUARIO, MENSAJE_IMPLEMENTAR))
    return True


def _resolver_alcance(
    resultado: ResultadoEjecucion,
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    alcance: Alcance,
    al_paso: Callable[[Paso], None] | None,
    al_aviso: Callable[[str], None] | None,
) -> None:
    """Justificar o revertir lo que cambió fuera del plan (con su propio presupuesto)."""
    ronda = resolver_fuera_del_plan(
        proveedor, mensajes, resultado.plan, alcance, resultado.alcance, resultado.pasos + 1,
        al_paso, al_aviso,
    )
    if ronda is not None:
        _acumular(resultado, ronda.pasos, ronda.tokens_entrada, ronda.tokens_salida)
        resultado.pasos_extra += ronda.pasos


def _hay_que_corregir_por_revision(
    resultado: ResultadoEjecucion,
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    revision: Revision | None,
    max_pasos: int,
    al_paso: Callable[[Paso], None] | None,
    al_aviso: Callable[[str], None] | None,
) -> bool:
    """Con los tests en verde: revisa y, si hay problemas, deja el pedido de corregirlos.

    Devuelve si el modelo tiene que volver a implementar.
    """
    estado = resultado.revision
    if revision is None or estado.vueltas >= revision.max_vueltas or estado.es_fallida:
        return False
    ronda = revisar(proveedor, mensajes, revision, resultado.pasos + 1, al_paso, al_aviso)
    _acumular(resultado, ronda.pasos, ronda.tokens_entrada, ronda.tokens_salida)
    # La revisión tiene su propio presupuesto: no le quita pasos a la implementación.
    resultado.pasos_extra += ronda.pasos
    estado.vueltas += 1
    if ronda.informe is None:
        estado.es_fallida = True
        estado.problemas = []
    else:
        estado.es_aprobada = ronda.informe.es_aprobado
        estado.problemas = [p.model_dump() for p in ronda.informe.problemas]
    es_corregir = (
        bool(estado.problemas)
        and estado.vueltas < revision.max_vueltas
        and resultado.pasos_con_presupuesto < max_pasos
    )
    if es_corregir:
        estado.correcciones += 1
        pedido = crear_mensaje_revision_con_problemas(estado.problemas)
        mensajes.append(Mensaje(Rol.USUARIO, pedido))
    if revision.al_revisar:
        revision.al_revisar(estado)
    return es_corregir


def _acumular(resultado: ResultadoEjecucion, pasos: int, entrada: int, salida: int) -> None:
    resultado.pasos += pasos
    resultado.tokens_entrada += entrada
    resultado.tokens_salida += salida
