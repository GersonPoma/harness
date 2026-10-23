"""Herramientas que el harness le ofrece al modelo."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

from harness.config_proyecto import ConfigProyecto
from harness.herramientas.archivos import escribir_archivo, leer_archivo, listar_directorio
from harness.herramientas.comandos import EjecutorComandos, ejecutar_comando
from harness.tipos import Herramienta

_PARAMETRO_RUTA = {
    "type": "string",
    "description": "Ruta relativa a la raíz del repo, con '/' como separador.",
}
_SIN_PARAMETROS = {"type": "object", "properties": {}}
NOMBRES_LECTURA = ("leer_archivo", "listar_directorio")


def crear_herramientas(
    repo: Path,
    config: ConfigProyecto | None = None,
    ejecutor: EjecutorComandos | None = None,
    al_escribir: Callable[[str], None] | None = None,
) -> list[Herramienta]:
    """Crea las herramientas atadas al repo objetivo.

    Sin harness.yaml (config None) solo hay herramientas de archivos, como en la fase 1.
    """
    protegidos = config.archivos_protegidos if config else []
    herramientas = _herramientas_de_archivos(repo, protegidos, al_escribir)
    if config and ejecutor:
        herramientas += _herramientas_de_comandos(config, ejecutor)
    return herramientas


def crear_herramientas_lectura(repo: Path) -> list[Herramienta]:
    """Solo las herramientas que no cambian nada: para la ronda de plan y para init."""
    return [h for h in _herramientas_de_archivos(repo, [], None) if h.nombre in NOMBRES_LECTURA]


def _herramientas_de_archivos(
    repo: Path, protegidos: list[str], al_escribir: Callable[[str], None] | None
) -> list[Herramienta]:
    # Funciones envoltorio que solo aceptan los argumentos del modelo: con partial, el modelo
    # podría mandar protegidos=[] como argumento y pisar la protección.
    def escribir(ruta: str, contenido: str) -> str:
        return escribir_archivo(repo, ruta, contenido, protegidos, al_escribir)

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
            funcion=escribir,
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


def _herramientas_de_comandos(
    config: ConfigProyecto, ejecutor: EjecutorComandos
) -> list[Herramienta]:
    tests, lint = config.comandos.tests, config.comandos.lint
    herramientas = [
        Herramienta(
            nombre="ejecutar_tests",
            descripcion=f"Corre los tests del proyecto ({tests}) y devuelve la salida.",
            parametros=_SIN_PARAMETROS,
            funcion=lambda: ejecutor.correr(tests)[1],
        )
    ]
    if lint:
        herramientas.append(Herramienta(
            nombre="ejecutar_lint",
            descripcion=f"Corre el linter del proyecto ({lint}) y devuelve la salida.",
            parametros=_SIN_PARAMETROS,
            funcion=lambda: ejecutor.correr(lint)[1],
        ))
    permitidos = config.comandos_permitidos
    if permitidos:

        def comando(comando: str) -> str:
            return ejecutar_comando(ejecutor, permitidos, comando)

        herramientas.append(Herramienta(
            nombre="ejecutar_comando",
            descripcion=(
                "Ejecuta un comando en el contenedor del proyecto, en la raíz del repo. "
                f"Solo programas permitidos: {', '.join(permitidos)}. "
                "No se puede encadenar ni redirigir (;, &, |, >, <)."
            ),
            parametros={
                "type": "object",
                "properties": {
                    "comando": {"type": "string", "description": "Ej: 'pytest -k crear'."}
                },
                "required": ["comando"],
            },
            funcion=comando,
        ))
    return herramientas
