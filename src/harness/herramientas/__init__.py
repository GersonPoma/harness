"""Herramientas que el harness le ofrece al modelo."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

from harness.config_proyecto import ConfigProyecto
from harness.herramientas.archivos import (
    editar_archivo,
    escribir_archivo,
    leer_archivo,
    listar_directorio,
)
from harness.herramientas.busqueda import buscar_en_codigo
from harness.herramientas.comandos import EjecutorComandos, ejecutar_comando
from harness.tipos import Herramienta

_PARAMETRO_RUTA = {
    "type": "string",
    "description": "Ruta relativa a la raíz del repo, con '/' como separador.",
}
_SIN_PARAMETROS = {"type": "object", "properties": {}}
NOMBRES_LECTURA = ("leer_archivo", "listar_directorio", "buscar_en_codigo")


def crear_herramientas(
    repo: Path,
    config: ConfigProyecto | None = None,
    ejecutor: EjecutorComandos | None = None,
    al_escribir: Callable[[str], None] | None = None,
    extras: list[Herramienta] | None = None,
) -> list[Herramienta]:
    """Crea las herramientas atadas al repo objetivo, más las extras (por ejemplo, las de MCP).

    Sin harness.yaml (config None) solo hay herramientas de archivos, como en la fase 1.
    """
    protegidos = config.archivos_protegidos if config else []
    herramientas = _herramientas_de_archivos(repo, protegidos, al_escribir)
    if config and ejecutor:
        herramientas += _herramientas_de_comandos(config, ejecutor)
    return herramientas + (extras or [])


def crear_herramientas_lectura(
    repo: Path, extras: list[Herramienta] | None = None
) -> list[Herramienta]:
    """Solo las que no cambian nada, para la ronda de plan y para init (extras: de solo lectura)."""
    lectura = [h for h in _herramientas_de_archivos(repo, [], None) if h.nombre in NOMBRES_LECTURA]
    return lectura + (extras or [])


def _herramientas_de_archivos(
    repo: Path, protegidos: list[str], al_escribir: Callable[[str], None] | None
) -> list[Herramienta]:
    # Funciones envoltorio que solo aceptan los argumentos del modelo: con partial, el modelo
    # podría mandar protegidos=[] como argumento y pisar la protección.
    def escribir(ruta: str, contenido: str) -> str:
        return escribir_archivo(repo, ruta, contenido, protegidos, al_escribir)

    def editar(
        ruta: str, texto_actual: str, texto_nuevo: str, reemplazar_todas: bool = False
    ) -> str:
        return editar_archivo(
            repo, ruta, texto_actual, texto_nuevo, reemplazar_todas, protegidos, al_escribir
        )

    return [
        Herramienta(
            nombre="leer_archivo",
            descripcion=(
                "Lee un archivo de texto del repo. Devuelve una primera línea 'Archivo: <ruta>' "
                "(no es parte del archivo) y después el contenido."
            ),
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
                "Crea un archivo del repo, o lo reemplaza por completo, con el contenido dado. "
                "Crea las carpetas que falten. Para cambiar parte de un archivo existente usa "
                "editar_archivo."
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
            nombre="editar_archivo",
            descripcion=(
                "Cambia parte de un archivo existente: reemplaza texto_actual (un fragmento "
                "exacto, copiado del archivo con sus espacios e indentación) por texto_nuevo. "
                "texto_actual tiene que aparecer una sola vez: si se repite, agrega líneas de "
                "contexto, o usa reemplazar_todas para cambiar todas las apariciones."
            ),
            parametros={
                "type": "object",
                "properties": {
                    "ruta": _PARAMETRO_RUTA,
                    "texto_actual": {
                        "type": "string",
                        "description": "Fragmento exacto que hay hoy en el archivo.",
                    },
                    "texto_nuevo": {
                        "type": "string",
                        "description": "Lo que va en su lugar (vacío para borrarlo).",
                    },
                    "reemplazar_todas": {
                        "type": "boolean",
                        "description": "Reemplazar todas las apariciones (por defecto, no).",
                    },
                },
                "required": ["ruta", "texto_actual", "texto_nuevo"],
            },
            funcion=editar,
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
        Herramienta(
            nombre="buscar_en_codigo",
            descripcion=(
                "Busca un texto en los archivos del repo y devuelve las líneas donde aparece, "
                "como 'ruta:línea: texto'. Sirve para encontrar dónde se define o se usa una "
                "clase o una función sin leer archivo por archivo."
            ),
            parametros={
                "type": "object",
                "properties": {
                    "patron": {"type": "string", "description": "Texto a buscar (exacto)."},
                    "ruta": {
                        **_PARAMETRO_RUTA,
                        "description": "Carpeta o archivo donde buscar; '.' es todo el repo.",
                    },
                    "es_regex": {
                        "type": "boolean",
                        "description": "Si patron es una expresión regular (por defecto, no).",
                    },
                },
                "required": ["patron"],
            },
            funcion=partial(buscar_en_codigo, repo),
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
