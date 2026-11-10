"""Paso de revisión: con los tests en verde, el modelo revisa el diff contra el ticket y las
reglas de AGENTS.md, y lo informa con la herramienta informar_revision.

Los tests no detectan una regla de arquitectura violada (TIENDA-3) ni código que nadie pidió
(TAREAS-3). La ronda solo lee; si encuentra problemas, el modelo vuelve a implementar.
"""

import difflib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from harness.bucle import Paso, ejecutar_bucle
from harness.cambios import CambiosRepo
from harness.git import ErrorGit, ejecutar_git
from harness.herramientas.archivos import ErrorHerramienta
from harness.plan import describir_error
from harness.prompts import (
    MENSAJE_ENTREGAR_REVISION,
    crear_mensaje_revision,
)
from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, Mensaje, Rol
from harness.variables import ocultar_secretos

NOMBRE_HERRAMIENTA_REVISION = "informar_revision"
# Un diff más largo se recorta: el modelo puede leer los archivos con leer_archivo.
MAX_CARACTERES_DIFF = 30_000
CONTEXTO_DIFF = 3


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ProblemaRevision(_Modelo):
    """Algo del diff que no cumple el ticket o AGENTS.md, o que nadie pidió."""

    archivo: str = Field(min_length=1)
    regla_o_criterio: str = Field(min_length=1)
    descripcion: str = Field(min_length=1)


class InformeRevision(_Modelo):
    """Lo que informa el modelo: aprobado sin problemas, o los problemas encontrados."""

    es_aprobado: bool
    problemas: list[ProblemaRevision] = []

    @model_validator(mode="after")
    def _coherente(self) -> "InformeRevision":
        if self.es_aprobado and self.problemas:
            raise ValueError("es_aprobado es true pero hay problemas: usa es_aprobado false")
        if not self.es_aprobado and not self.problemas:
            raise ValueError("es_aprobado es false pero no hay problemas: describe cada uno")
        return self


@dataclass
class EstadoRevision:
    """Cómo va la revisión de la ejecución (también se guarda en la sesión)."""

    vueltas: int = 0
    # Revisiones con problemas después de las que el modelo volvió a implementar.
    correcciones: int = 0
    # Los de la última revisión: si no está aprobada, quedan sin resolver.
    problemas: list[dict[str, str]] = field(default_factory=list)
    es_aprobada: bool = False
    # El modelo no llamó a informar_revision: se siguió sin revisión.
    es_fallida: bool = False


@dataclass
class Revision:
    """Cómo se revisa: herramientas de lectura, el diff y los límites de harness.yaml."""

    herramientas_lectura: list[Herramienta]
    obtener_diff: Callable[[], str]
    max_vueltas: int
    max_pasos: int
    al_revisar: Callable[[EstadoRevision], None] | None = None


@dataclass
class ResultadoRonda:
    """El informe (None si el modelo no lo dio) y lo que costó."""

    informe: InformeRevision | None
    pasos: int
    tokens_entrada: int
    tokens_salida: int


def revisar(
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    revision: Revision,
    primer_paso: int,
    al_paso: Callable[[Paso], None] | None = None,
    al_aviso: Callable[[str], None] | None = None,
    al_respuesta: Callable[[int, str], None] | None = None,
) -> ResultadoRonda:
    """Una ronda de revisión: lee lo que necesite y llama a informar_revision.

    El último paso se reserva para pedir el informe con solo esa herramienta (tool_choice).
    """
    informes: list[InformeRevision] = []
    herramienta = crear_herramienta_revision(informes.append)
    mensajes.append(Mensaje(Rol.USUARIO, crear_mensaje_revision(revision.obtener_diff())))
    explorar = max(revision.max_pasos - 1, 1)
    bucle = ejecutar_bucle(
        proveedor, mensajes, [*revision.herramientas_lectura, herramienta], explorar, al_paso,
        primer_paso=primer_paso, al_aviso=al_aviso, es_fin=lambda: bool(informes),
        al_respuesta=al_respuesta,
    )
    pasos, entrada, salida = bucle.pasos, bucle.tokens_entrada, bucle.tokens_salida
    if not informes:
        mensajes.append(Mensaje(Rol.USUARIO, MENSAJE_ENTREGAR_REVISION))
        extra = ejecutar_bucle(
            proveedor, mensajes, [herramienta], 1, al_paso, primer_paso=primer_paso + pasos,
            al_aviso=al_aviso, es_fin=lambda: bool(informes),
            herramienta_obligatoria=NOMBRE_HERRAMIENTA_REVISION, al_respuesta=al_respuesta,
        )
        pasos += extra.pasos
        entrada += extra.tokens_entrada
        salida += extra.tokens_salida
    return ResultadoRonda(informes[-1] if informes else None, pasos, entrada, salida)


def crear_herramienta_revision(al_informar: Callable[[InformeRevision], None]) -> Herramienta:
    """Herramienta informar_revision: valida el informe y se lo entrega a `al_informar`."""

    def informar_revision(**argumentos: Any) -> str:
        try:
            informe = InformeRevision.model_validate(argumentos)
        except ValidationError as error:
            raise ErrorHerramienta(f"Informe inválido: {describir_error(error)}") from error
        al_informar(informe)
        return "Revisión registrada."

    return Herramienta(
        nombre=NOMBRE_HERRAMIENTA_REVISION,
        descripcion=(
            "Informa el resultado de la revisión: es_aprobado true si el diff cumple el ticket "
            "y AGENTS.md, o false con cada problema encontrado."
        ),
        parametros=PARAMETROS_REVISION,
        funcion=informar_revision,
    )


PARAMETROS_REVISION = {
    "type": "object",
    "properties": {
        "es_aprobado": {
            "type": "boolean",
            "description": "true si no hay ningún problema; false si hay que corregir algo.",
        },
        "problemas": {
            "type": "array",
            "description": "Cada problema encontrado (vacío si es_aprobado es true).",
            "items": {
                "type": "object",
                "properties": {
                    "archivo": {"type": "string", "description": "Ruta del archivo."},
                    "regla_o_criterio": {
                        "type": "string",
                        "description": (
                            "La regla de AGENTS.md o el criterio del ticket que no se cumple, "
                            "o 'fuera del ticket' si es código que ningún criterio pide."
                        ),
                    },
                    "descripcion": {"type": "string", "description": "Qué está mal y dónde."},
                },
                "required": ["archivo", "regla_o_criterio", "descripcion"],
            },
        },
    },
    "required": ["es_aprobado", "problemas"],
}


def armar_diff(repo: Path, cambios: CambiosRepo, secretos: list[str]) -> str:
    """El diff de trabajo de la ejecución: modificados y eliminados contra HEAD, y los creados
    completos. Sin la memoria (la revisa la persona en el PR) y con los secretos ocultos."""
    partes = []
    existentes = cambios.modificados + cambios.eliminados
    if existentes:
        partes.append(_diff_contra_head(repo, existentes))
    partes += [_diff_de_nuevo(repo, ruta) for ruta in cambios.creados]
    diff = ocultar_secretos("\n".join(p for p in partes if p), secretos)
    if not diff:
        return "(sin cambios en el código)"
    if len(diff) > MAX_CARACTERES_DIFF:
        return (
            f"{diff[:MAX_CARACTERES_DIFF]}\n[... diff recortado: se muestran "
            f"{MAX_CARACTERES_DIFF} de {len(diff)} caracteres; lee el resto con leer_archivo]"
        )
    return diff


def _diff_contra_head(repo: Path, rutas: list[str]) -> str:
    try:
        return ejecutar_git(repo, "diff", "HEAD", "--", *rutas)
    except ErrorGit:
        # Sin Git (o sin commits): al menos qué archivos son.
        return "\n".join(f"(cambió {ruta}; sin Git no hay diff)" for ruta in rutas)


def _diff_de_nuevo(repo: Path, ruta: str) -> str:
    try:
        lineas = (repo / ruta).read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return f"(archivo nuevo {ruta}, no es texto)"
    diferencia = difflib.unified_diff(
        [], lineas, "/dev/null", f"b/{ruta}", n=CONTEXTO_DIFF, lineterm=""
    )
    return "\n".join(linea.rstrip("\n") for linea in diferencia)
