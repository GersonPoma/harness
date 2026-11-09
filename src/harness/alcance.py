"""Alcance de más: antes de validar, el modelo justifica o revierte cada archivo que cambió
sin estar en el plan.

Caso real, TAREAS-3: al reescribir main.py el modelo agregó POST /tareas (de otro ticket) y,
para sostenerlo, un modelo nuevo; los tests pasaron y el PR llevaba un endpoint que nadie
pidió. Solo se avisaba "Archivos que cambiaron y no estaban en el plan".
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from harness.bucle import Paso, ejecutar_bucle
from harness.cambios import CambiosRepo, Instantanea, comparar, tomar_instantanea
from harness.git import ErrorGit, leer_de_head
from harness.herramientas.archivos import ErrorHerramienta
from harness.plan import Plan, archivos_fuera_del_plan
from harness.prompts import crear_mensaje_fuera_del_plan
from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, Mensaje, Rol

# Pasos de la ronda: uno por archivo, más dos para leer o equivocarse. No descuentan de
# max_pasos, como la revisión.
PASOS_BASE_ALCANCE = 2


@dataclass
class EstadoAlcance:
    """Qué se hizo con los archivos fuera del plan (también se guarda en la sesión)."""

    justificados: dict[str, str] = field(default_factory=dict)
    revertidos: list[str] = field(default_factory=list)
    # Los que el modelo no justificó ni revirtió antes de quedarse sin pasos.
    pendientes: list[str] = field(default_factory=list)

    @property
    def hay_archivos(self) -> bool:
        """True si algo cambió fuera del plan (justificado, revertido o pendiente)."""
        return bool(self.justificados or self.revertidos or self.pendientes)


@dataclass
class Alcance:
    """Lo que necesita la ronda: el repo, su foto al empezar y las herramientas de lectura."""

    repo: Path
    antes: Instantanea
    herramientas_lectura: list[Herramienta]
    # Avisa de un archivo restaurado (el entorno Docker reinstala si es de dependencias).
    al_escribir: Callable[[str], None] | None = None
    al_resolver: Callable[[EstadoAlcance], None] | None = None


@dataclass
class ResultadoRondaAlcance:
    pasos: int
    tokens_entrada: int
    tokens_salida: int


def resolver_fuera_del_plan(
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    plan: Plan,
    alcance: Alcance,
    estado: EstadoAlcance,
    primer_paso: int,
    al_paso: Callable[[Paso], None] | None = None,
    al_aviso: Callable[[str], None] | None = None,
) -> ResultadoRondaAlcance | None:
    """La ronda de justificar o revertir; None si no hay nada fuera del plan sin resolver."""
    pendientes = _sin_resolver(plan, _cambios(alcance), estado)
    if not pendientes:
        return None
    mensajes.append(Mensaje(Rol.USUARIO, crear_mensaje_fuera_del_plan(pendientes)))
    herramientas = crear_herramientas_alcance(alcance, pendientes, estado)
    bucle = ejecutar_bucle(
        proveedor, mensajes, [*alcance.herramientas_lectura, *herramientas],
        PASOS_BASE_ALCANCE + len(pendientes), al_paso, primer_paso=primer_paso,
        al_aviso=al_aviso, es_fin=lambda: not _sin_resolver(plan, _cambios(alcance), estado),
    )
    estado.pendientes = _sin_resolver(plan, _cambios(alcance), estado)
    if alcance.al_resolver:
        alcance.al_resolver(estado)
    return ResultadoRondaAlcance(bucle.pasos, bucle.tokens_entrada, bucle.tokens_salida)


def crear_herramientas_alcance(
    alcance: Alcance, pendientes: list[str], estado: EstadoAlcance
) -> list[Herramienta]:
    """justificar_cambio y revertir_archivo, solo para los archivos fuera del plan."""

    def justificar_cambio(ruta: str, motivo: str) -> str:
        ruta = _normalizar(ruta)
        _exigir_pendiente(ruta, pendientes)
        if not motivo.strip():
            raise ErrorHerramienta("El motivo está vacío: di qué criterio del ticket lo pide.")
        estado.justificados[ruta] = motivo.strip()
        return f"Cambio en '{ruta}' justificado; va en la descripción del PR."

    def revertir_archivo(ruta: str) -> str:
        ruta = _normalizar(ruta)
        _exigir_pendiente(ruta, pendientes)
        revertir(alcance, ruta)
        estado.revertidos.append(ruta)
        return f"'{ruta}' quedó como estaba antes de esta ejecución."

    parametro_ruta = {"type": "string", "description": "Uno de los archivos fuera del plan."}
    return [
        Herramienta(
            nombre="justificar_cambio",
            descripcion=(
                "Explica por qué un archivo fuera del plan tenía que cambiar: qué criterio del "
                "ticket lo pide. La justificación va en la descripción del PR."
            ),
            parametros={
                "type": "object",
                "properties": {
                    "ruta": parametro_ruta,
                    "motivo": {"type": "string", "description": "El criterio que lo pide."},
                },
                "required": ["ruta", "motivo"],
            },
            funcion=justificar_cambio,
        ),
        Herramienta(
            nombre="revertir_archivo",
            descripcion=(
                "Deja un archivo fuera del plan como estaba antes de esta ejecución (si lo "
                "creaste, lo borra). Úsala para el código que ningún criterio pide."
            ),
            parametros={
                "type": "object", "properties": {"ruta": parametro_ruta}, "required": ["ruta"],
            },
            funcion=revertir_archivo,
        ),
    ]


def revertir(alcance: Alcance, ruta: str) -> None:
    """Deja `ruta` como en la foto del principio de la ejecución.

    Solo un archivo que cambió durante esta ejecución (según la foto de cambios): nunca otro.
    Para restaurarlo se usa HEAD, y solo si HEAD tiene exactamente lo que había al empezar
    (si no, se perderían cambios anteriores a la ejecución).
    """
    cambios = _cambios(alcance)
    cambiados = cambios.creados + cambios.modificados + cambios.eliminados
    if ruta not in cambiados:
        raise ErrorHerramienta(
            f"'{ruta}' no cambió durante esta ejecución: no se puede revertir."
        )
    archivo = alcance.repo / ruta
    if ruta in cambios.creados:
        archivo.unlink()
    else:
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_bytes(_contenido_original(alcance, ruta))
    if alcance.al_escribir:
        alcance.al_escribir(ruta)


def _contenido_original(alcance: Alcance, ruta: str) -> bytes:
    try:
        contenido = leer_de_head(alcance.repo, ruta)
    except ErrorGit as error:
        raise ErrorHerramienta(
            f"No se puede revertir '{ruta}' sin Git: justifícalo con justificar_cambio."
        ) from error
    if contenido is None or hashlib.sha256(contenido).hexdigest() != alcance.antes.get(ruta):
        raise ErrorHerramienta(
            f"No se puede revertir '{ruta}' con seguridad (al empezar no estaba como en el "
            "último commit): deshaz el cambio con editar_archivo o justifícalo."
        )
    return contenido


def _cambios(alcance: Alcance) -> CambiosRepo:
    return comparar(alcance.antes, tomar_instantanea(alcance.repo))


def _sin_resolver(plan: Plan, cambios: CambiosRepo, estado: EstadoAlcance) -> list[str]:
    return [
        ruta for ruta in archivos_fuera_del_plan(plan, cambios)
        if ruta not in estado.justificados
    ]


def _exigir_pendiente(ruta: str, pendientes: list[str]) -> None:
    if ruta not in pendientes:
        raise ErrorHerramienta(
            f"'{ruta}' no es uno de los archivos fuera del plan: {', '.join(pendientes)}."
        )


def _normalizar(ruta: str) -> str:
    ruta = ruta.replace("\\", "/")
    while ruta.startswith("./"):
        ruta = ruta[2:]
    return ruta
