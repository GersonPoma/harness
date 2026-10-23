"""Plan que el modelo propone antes de implementar, con la herramienta proponer_plan."""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from harness.cambios import CambiosRepo
from harness.herramientas.archivos import ErrorHerramienta
from harness.tipos import Herramienta

NOMBRE_HERRAMIENTA_PLAN = "proponer_plan"
ACCIONES = ("crear", "modificar", "eliminar")

Accion = Literal["crear", "modificar", "eliminar"]
TextoNoVacio = Field(min_length=1)


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ArchivoPlan(_Modelo):
    """Un archivo que el plan va a tocar."""

    ruta: str = TextoNoVacio
    accion: Accion
    motivo: str = TextoNoVacio

    @field_validator("ruta")
    @classmethod
    def _normalizar_ruta(cls, valor: str) -> str:
        # Se compara con las rutas de la foto de cambios: siempre con '/' y sin './'.
        valor = valor.replace("\\", "/")
        while valor.startswith("./"):
            valor = valor[2:]
        return valor


class Plan(_Modelo):
    """Qué se va a hacer, en qué archivos, en qué orden y con qué tests."""

    resumen: str = TextoNoVacio
    archivos: list[ArchivoPlan] = Field(min_length=1)
    pasos: list[str] = Field(min_length=1)
    tests: list[str] = []


def crear_herramienta_plan(al_proponer: Callable[[Plan], None]) -> Herramienta:
    """Herramienta proponer_plan: valida el plan y se lo entrega a `al_proponer`."""

    def proponer_plan(**argumentos: Any) -> str:
        try:
            plan = Plan.model_validate(argumentos)
        except ValidationError as error:
            raise ErrorHerramienta(f"Plan inválido: {_describir(error)}") from error
        al_proponer(plan)
        return "Plan registrado."

    return Herramienta(
        nombre=NOMBRE_HERRAMIENTA_PLAN,
        descripcion=(
            "Registra tu plan para el ticket antes de implementarlo. Llámala una sola vez, "
            "cuando ya sepas qué archivos vas a tocar."
        ),
        parametros=PARAMETROS_PLAN,
        funcion=proponer_plan,
    )


PARAMETROS_PLAN = {
    "type": "object",
    "properties": {
        "resumen": {
            "type": "string",
            "description": "Qué vas a hacer, en una o dos oraciones.",
        },
        "archivos": {
            "type": "array",
            "description": "Archivos que vas a crear, modificar o eliminar.",
            "items": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Relativa a la raíz del repo."},
                    "accion": {"type": "string", "enum": list(ACCIONES)},
                    "motivo": {"type": "string", "description": "Por qué hay que tocarlo."},
                },
                "required": ["ruta", "accion", "motivo"],
            },
        },
        "pasos": {
            "type": "array",
            "description": "Pasos de la implementación, en orden.",
            "items": {"type": "string"},
        },
        "tests": {
            "type": "array",
            "description": "Tests que vas a agregar o cambiar.",
            "items": {"type": "string"},
        },
    },
    "required": ["resumen", "archivos", "pasos", "tests"],
}


def archivos_fuera_del_plan(plan: Plan, cambios: CambiosRepo) -> list[str]:
    """Archivos que cambiaron y que el plan no mencionaba."""
    planeados = {archivo.ruta.lower() for archivo in plan.archivos}
    tocados = cambios.creados + cambios.modificados + cambios.eliminados
    # En minúsculas porque en Windows 'App.py' y 'app.py' son el mismo archivo.
    return sorted(ruta for ruta in tocados if ruta.lower() not in planeados)


def _describir(error: ValidationError) -> str:
    partes = []
    for detalle in error.errors():
        ruta = ".".join(str(parte) for parte in detalle["loc"]) or "(raíz)"
        partes.append(f"{ruta}: {detalle['msg']}")
    return "; ".join(partes)
