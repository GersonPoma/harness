"""Herramientas que el harness le ofrece al modelo."""

from functools import partial
from pathlib import Path

from harness.herramientas.archivos import escribir_archivo, leer_archivo, listar_directorio
from harness.tipos import Herramienta

_PARAMETRO_RUTA = {
    "type": "string",
    "description": "Ruta relativa a la raíz del repo, con '/' como separador.",
}


def crear_herramientas(repo: Path) -> list[Herramienta]:
    """Crea las herramientas de archivos atadas al repo objetivo."""
    return [
        Herramienta(
            nombre="leer_archivo",
            descripcion="Lee un archivo de texto del repo y devuelve su contenido.",
            parametros={
                "type": "object",
                "properties": {"ruta": _PARAMETRO_RUTA},
                "required": ["ruta"],
            },
            funcion=partial(leer_archivo, repo),
        ),
        Herramienta(
            nombre="escribir_archivo",
            descripcion=(
                "Crea o reemplaza por completo un archivo del repo con el contenido dado. "
                "Crea las carpetas que falten."
            ),
            parametros={
                "type": "object",
                "properties": {
                    "ruta": _PARAMETRO_RUTA,
                    "contenido": {
                        "type": "string",
                        "description": "Contenido completo del archivo.",
                    },
                },
                "required": ["ruta", "contenido"],
            },
            funcion=partial(escribir_archivo, repo),
        ),
        Herramienta(
            nombre="listar_directorio",
            descripcion=(
                "Lista archivos y carpetas de una carpeta del repo (no recursivo). "
                "Las carpetas terminan en '/'."
            ),
            parametros={
                "type": "object",
                "properties": {
                    "ruta": {**_PARAMETRO_RUTA, "description": "Carpeta a listar; '.' es la raíz."}
                },
            },
            funcion=partial(listar_directorio, repo),
        ),
    ]
