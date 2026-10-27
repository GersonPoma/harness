"""harness init: el modelo analiza el repo y escribe los AGENTS.md / harness.yaml que falten.

Nunca sobrescribe un archivo con contenido: los dos los revisa una persona antes de usarlos
(harness.yaml decide qué comandos se ejecutan). Uno vacío cuenta como faltante. No mira
requiere_agents_md: esa clave solo afecta a harness run.
"""

import re

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from harness.bucle import Paso, ResultadoBucle, ejecutar_bucle
from harness.config_proyecto import (
    NOMBRE_ARCHIVO,
    ConfigProyecto,
    ErrorConfigProyecto,
    validar_texto_config,
)
from harness.contexto import NOMBRE_AGENTS
from harness.entorno_docker import EntornoDocker, ResultadoComando
from harness.herramientas import crear_herramientas_lectura
from harness.herramientas.archivos import ErrorHerramienta
from harness.prompts import crear_mensajes_init, crear_mensaje_guardar_faltantes
from harness.proveedores.base import ProveedorLLM
from harness.tipos import Herramienta, Mensaje, Rol
from harness.variables import VariablesResueltas

ARCHIVOS_INIT = (NOMBRE_AGENTS, NOMBRE_ARCHIVO)
HERRAMIENTA_GUARDAR = {
    NOMBRE_AGENTS: "guardar_agents_md",
    NOMBRE_ARCHIVO: "guardar_harness_yaml",
}
MAX_PASOS_INIT = 30
# Pasos para el último pedido ("guarda lo que falta"), con solo las herramientas de guardar.
MAX_PASOS_GUARDAR = 2
NOMBRE_EJECUCION_VERIFICAR = "init"
CLAVE_REQUIERE_AGENTS = "requiere_agents_md"
LINEA_REQUIERE_AGENTS = (
    f"{CLAVE_REQUIERE_AGENTS}: false  # true = harness run se detiene si falta AGENTS.md\n"
)
PATRON_LINEA_PROYECTO = re.compile(r"^proyecto:.*(?:\n|$)", re.MULTILINE)


class ErrorInit(Exception):
    """El modelo no guardó todos los archivos que faltaban."""


@dataclass
class ResultadoInit:
    """Qué archivos se crearon y cuánto costó."""

    creados: list[str] = field(default_factory=list)
    pasos: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    texto_final: str | None = None


def archivos_faltantes(repo: Path) -> list[str]:
    """AGENTS.md y harness.yaml que el repo todavía no tiene (o tiene vacíos)."""
    return [nombre for nombre in ARCHIVOS_INIT if _falta(repo / nombre)]


def ejecutar_init(
    proveedor: ProveedorLLM,
    repo: Path,
    al_paso: Callable[[Paso], None] | None = None,
    al_aviso: Callable[[str], None] | None = None,
) -> ResultadoInit:
    """Pide al modelo los archivos que faltan; termina cuando los guardó todos."""
    faltantes = archivos_faltantes(repo)
    resultado = ResultadoInit()
    if not faltantes:
        return resultado
    existentes = {
        nombre: (repo / nombre).read_text(encoding="utf-8-sig", errors="replace")
        for nombre in ARCHIVOS_INIT if nombre not in faltantes
    }
    guardar = crear_herramientas_guardar(repo, faltantes, resultado.creados.append)

    def es_fin() -> bool:
        return set(resultado.creados) >= set(faltantes)

    mensajes = crear_mensajes_init(faltantes, existentes)
    bucle = ejecutar_bucle(
        proveedor, mensajes, [*crear_herramientas_lectura(repo), *guardar], MAX_PASOS_INIT,
        al_paso, al_aviso=al_aviso, es_fin=es_fin,
    )
    _acumular(resultado, bucle)
    if not es_fin():
        pendientes = [nombre for nombre in faltantes if nombre not in resultado.creados]
        if al_aviso:
            al_aviso(f"Faltan por guardar: {', '.join(pendientes)}. Se le pide al modelo.")
        mensajes.append(Mensaje(Rol.USUARIO, crear_mensaje_guardar_faltantes(pendientes)))
        extra = ejecutar_bucle(
            proveedor, mensajes, _solo_para(guardar, pendientes), MAX_PASOS_GUARDAR, al_paso,
            primer_paso=resultado.pasos + 1, al_aviso=al_aviso, es_fin=es_fin,
        )
        _acumular(resultado, extra)
    if not es_fin():
        pendientes = [nombre for nombre in faltantes if nombre not in resultado.creados]
        raise ErrorInit(f"El modelo no guardó: {', '.join(pendientes)}.")
    return resultado


def crear_herramientas_guardar(
    repo: Path, faltantes: list[str], al_guardar: Callable[[str], None]
) -> list[Herramienta]:
    """Una herramienta por archivo que falta; solo esas pueden escribir en el repo."""
    herramientas = []
    if NOMBRE_AGENTS in faltantes:
        def guardar_agents_md(contenido: str) -> str:
            return _guardar(repo, NOMBRE_AGENTS, contenido, al_guardar)

        herramientas.append(_herramienta_guardar(
            HERRAMIENTA_GUARDAR[NOMBRE_AGENTS],
            "Guarda AGENTS.md en la raíz del repo (Markdown, en español).",
            guardar_agents_md,
        ))
    if NOMBRE_ARCHIVO in faltantes:
        def guardar_harness_yaml(contenido: str) -> str:
            try:
                validar_texto_config(contenido)
            except ErrorConfigProyecto as error:
                raise ErrorHerramienta(f"{error}\nCorrige el YAML y vuelve a llamar.") from error
            return _guardar(repo, NOMBRE_ARCHIVO, _con_requiere_agents(contenido), al_guardar)

        herramientas.append(_herramienta_guardar(
            HERRAMIENTA_GUARDAR[NOMBRE_ARCHIVO],
            "Valida y guarda harness.yaml en la raíz del repo. Si no cumple el formato, "
            "devuelve los errores y no guarda nada.",
            guardar_harness_yaml,
        ))
    return herramientas


def verificar_config(
    repo: Path,
    config: ConfigProyecto,
    variables: VariablesResueltas,
    al_avanzar: Callable[[str], None] | None = None,
) -> ResultadoComando:
    """Levanta el entorno con el harness.yaml (servicios, instalación) y corre los tests.

    Solo informa: no hay bucle de corrección del YAML (queda para la fase 9).
    """
    with EntornoDocker(repo, config, NOMBRE_EJECUCION_VERIFICAR, variables) as entorno:
        entorno.iniciar(al_avanzar)
        if al_avanzar:
            al_avanzar(f"Corriendo los tests ({config.comandos.tests})...")
        return entorno.ejecutar(config.comandos.tests)


def _falta(ruta: Path) -> bool:
    if not ruta.is_file():
        return True
    return not ruta.read_text(encoding="utf-8-sig", errors="replace").strip()


def _con_requiere_agents(contenido: str) -> str:
    """Agrega requiere_agents_md explícita si el modelo no la puso, para que se vea que existe."""
    if CLAVE_REQUIERE_AGENTS in yaml.safe_load(contenido):
        return contenido
    linea_proyecto = PATRON_LINEA_PROYECTO.search(contenido)
    if linea_proyecto is None:
        return contenido.rstrip("\n") + "\n" + LINEA_REQUIERE_AGENTS
    fin = linea_proyecto.end()
    separador = "" if contenido[fin - 1:fin] == "\n" else "\n"
    return contenido[:fin] + separador + LINEA_REQUIERE_AGENTS + contenido[fin:]


def _solo_para(guardar: list[Herramienta], pendientes: list[str]) -> list[Herramienta]:
    nombres = {HERRAMIENTA_GUARDAR[nombre] for nombre in pendientes}
    return [herramienta for herramienta in guardar if herramienta.nombre in nombres]


def _herramienta_guardar(
    nombre: str, descripcion: str, funcion: Callable[[str], str]
) -> Herramienta:
    return Herramienta(
        nombre=nombre,
        descripcion=descripcion,
        parametros={
            "type": "object",
            "properties": {
                "contenido": {"type": "string", "description": "Contenido completo."},
            },
            "required": ["contenido"],
        },
        funcion=funcion,
    )


def _guardar(repo: Path, nombre: str, contenido: str, al_guardar: Callable[[str], None]) -> str:
    # newline="" para escribir los saltos de línea tal cual vienen, también en Windows.
    (repo / nombre).write_text(contenido, encoding="utf-8", newline="")
    al_guardar(nombre)
    return f"{nombre} guardado ({len(contenido)} caracteres)."


def _acumular(resultado: ResultadoInit, bucle: ResultadoBucle) -> None:
    resultado.pasos += bucle.pasos
    resultado.tokens_entrada += bucle.tokens_entrada
    resultado.tokens_salida += bucle.tokens_salida
    resultado.texto_final = bucle.texto_final or resultado.texto_final
