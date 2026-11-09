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
from harness.cache_dependencias import EstadoCache
from harness.entorno_docker import EntornoDocker, Instalacion, ResultadoComando
from harness.herramientas import crear_herramientas_lectura
from harness.herramientas.archivos import ErrorHerramienta
from harness.prompts import (
    crear_mensaje_corregir_harness_yaml,
    crear_mensaje_guardar_faltantes,
    crear_mensajes_init,
)
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
# init --verificar: cuántas veces se le devuelve el error al modelo, y sus pasos cada vez.
MAX_CORRECCIONES_INIT = 2
MAX_PASOS_CORRECCION_INIT = 6
NOMBRE_EJECUCION_VERIFICAR = "init"
CLAVE_REQUIERE_AGENTS = "requiere_agents_md"
LINEA_REQUIERE_AGENTS = (
    f"{CLAVE_REQUIERE_AGENTS}: false  # true = harness run se detiene si falta AGENTS.md\n"
)
PATRON_LINEA_PROYECTO = re.compile(r"^proyecto:.*(?:\n|$)", re.MULTILINE)
NOMBRE_GITIGNORE = ".gitignore"
LINEAS_GITIGNORE = (".harness/logs/", ".harness/sessions/")
COMENTARIO_GITIGNORE = "# Datos locales del harness (.harness/memory.md sí se sube)"


@dataclass(frozen=True)
class MinimosStack:
    """Límites recomendados para un stack compilado (ver el prompt de init)."""

    timeout_comando_segundos: int
    max_caracteres_salida: int


MINIMOS_GRADLE = MinimosStack(timeout_comando_segundos=1200, max_caracteres_salida=6000)
MINIMOS_MAVEN = MinimosStack(timeout_comando_segundos=600, max_caracteres_salida=6000)
BLOQUES_TEST_LOGGING = {
    "build.gradle.kts": (
        "tasks.test {\n"
        "    testLogging {\n"
        "        events(\"failed\")\n"
        "        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL\n"
        "    }\n"
        "}"
    ),
    "build.gradle": (
        "test {\n"
        "    testLogging {\n"
        "        events \"failed\"\n"
        "        exceptionFormat \"full\"\n"
        "    }\n"
        "}"
    ),
}


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
    # La conversación, para devolverle al modelo el error de --verificar.
    mensajes: list[Mensaje] = field(default_factory=list)


@dataclass
class CorreccionInit:
    """Qué hizo el modelo con el error de --verificar: guardar otro harness.yaml o no."""

    es_guardado: bool
    texto_final: str | None
    pasos: int
    tokens_entrada: int
    tokens_salida: int


def asegurar_gitignore(repo: Path) -> list[str]:
    """Agrega al .gitignore (o lo crea) los logs y las sesiones del harness, si faltan.

    Solo esas dos: .harness/memory.md se sube a Git, así que .harness/ entero no se ignora.
    Nunca reescribe lo que ya tiene el archivo. Devuelve las líneas agregadas.
    """
    ruta = repo / NOMBRE_GITIGNORE
    actual = ruta.read_text(encoding="utf-8-sig", errors="replace") if ruta.is_file() else ""
    presentes = {linea.strip().rstrip("/") for linea in actual.splitlines()}
    faltantes = [linea for linea in LINEAS_GITIGNORE if linea.rstrip("/") not in presentes]
    if not faltantes:
        return []
    separador = "\n" if actual and not actual.endswith("\n") else ""
    bloque = "\n".join([COMENTARIO_GITIGNORE, *faltantes])
    espacio = "\n" if actual.strip() else ""
    ruta.write_text(f"{actual}{separador}{espacio}{bloque}\n", encoding="utf-8")
    return faltantes


def archivos_faltantes(repo: Path) -> list[str]:
    """AGENTS.md y harness.yaml que el repo todavía no tiene (o tiene vacíos)."""
    return [nombre for nombre in ARCHIVOS_INIT if _falta(repo / nombre)]


def ejecutar_init(
    proveedor: ProveedorLLM,
    repo: Path,
    al_paso: Callable[[Paso], None] | None = None,
    al_aviso: Callable[[str], None] | None = None,
    imagenes: list[str] | None = None,
) -> ResultadoInit:
    """Pide al modelo los archivos que faltan; termina cuando los guardó todos.

    `imagenes`: las de Docker ya descargadas, para que el modelo las prefiera.
    """
    faltantes = archivos_faltantes(repo)
    resultado = ResultadoInit()
    if not faltantes:
        return resultado
    existentes = {
        nombre: (repo / nombre).read_text(encoding="utf-8-sig", errors="replace")
        for nombre in ARCHIVOS_INIT if nombre not in faltantes
    }
    guardar = crear_herramientas_guardar(repo, faltantes, resultado.creados.append, al_aviso)

    def es_fin() -> bool:
        return set(resultado.creados) >= set(faltantes)

    mensajes = crear_mensajes_init(faltantes, existentes, imagenes)
    resultado.mensajes = mensajes
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


def corregir_harness_yaml(
    proveedor: ProveedorLLM,
    mensajes: list[Mensaje],
    repo: Path,
    error: str,
    primer_paso: int = 1,
    al_paso: Callable[[Paso], None] | None = None,
    al_aviso: Callable[[str], None] | None = None,
) -> CorreccionInit:
    """Le devuelve al modelo el error de --verificar para que corrija el harness.yaml que
    escribió; puede responder con texto si el problema no es del YAML."""
    guardados: list[str] = []
    guardar = crear_herramientas_guardar(repo, [NOMBRE_ARCHIVO], guardados.append, al_aviso)
    mensajes.append(Mensaje(Rol.USUARIO, crear_mensaje_corregir_harness_yaml(error)))
    bucle = ejecutar_bucle(
        proveedor, mensajes, [*crear_herramientas_lectura(repo), *guardar],
        MAX_PASOS_CORRECCION_INIT, al_paso, primer_paso=primer_paso, al_aviso=al_aviso,
        es_fin=lambda: bool(guardados),
    )
    return CorreccionInit(
        bool(guardados), bucle.texto_final, bucle.pasos, bucle.tokens_entrada,
        bucle.tokens_salida,
    )


def crear_herramientas_guardar(
    repo: Path,
    faltantes: list[str],
    al_guardar: Callable[[str], None],
    al_aviso: Callable[[str], None] | None = None,
) -> list[Herramienta]:
    """Una herramienta por archivo que falta; solo esas pueden escribir en el repo.

    guardar_harness_yaml no guarda, la primera vez, un YAML con límites por debajo de lo
    recomendado para su stack: el modelo lo corrige o insiste con ignorar_advertencias.
    """
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
        def guardar_harness_yaml(contenido: str, ignorar_advertencias: bool = False) -> str:
            try:
                config = validar_texto_config(contenido)
            except ErrorConfigProyecto as error:
                raise ErrorHerramienta(f"{error}\nCorrige el YAML y vuelve a llamar.") from error
            advertencias = advertencias_de_limites(config)
            if advertencias and not ignorar_advertencias:
                lista = "\n".join(f"- {advertencia}" for advertencia in advertencias)
                raise ErrorHerramienta(
                    f"No se guardó: hay límites por debajo de lo recomendado.\n{lista}\n"
                    "Corrígelos, o vuelve a llamar con ignorar_advertencias: true si tienes un "
                    "motivo para dejarlos así."
                )
            if advertencias and al_aviso:
                al_aviso(
                    f"{NOMBRE_ARCHIVO} se guardó con advertencias (el modelo las ignoró): "
                    + " ".join(advertencias)
                )
            return _guardar(repo, NOMBRE_ARCHIVO, _con_requiere_agents(contenido), al_guardar)

        herramientas.append(_herramienta_guardar(
            HERRAMIENTA_GUARDAR[NOMBRE_ARCHIVO],
            "Valida y guarda harness.yaml en la raíz del repo. Si no cumple el formato, o si "
            "sus límites están por debajo de lo recomendado para el stack, devuelve los "
            "errores o las advertencias y no guarda nada.",
            guardar_harness_yaml,
            con_ignorar_advertencias=True,
        ))
    return herramientas


def verificar_config(
    repo: Path,
    config: ConfigProyecto,
    variables: VariablesResueltas,
    al_avanzar: Callable[[str], None] | None = None,
    usar_cache: bool = True,
    al_cache: Callable[[EstadoCache], None] | None = None,
    al_instalar: Callable[[Instalacion], None] | None = None,
) -> ResultadoComando:
    """Levanta el entorno con el harness.yaml (servicios, instalación) y corre los tests.

    Solo informa; si el harness.yaml lo generó init, la CLI le devuelve el error al modelo
    (corregir_harness_yaml).
    """
    entorno_docker = EntornoDocker(
        repo,
        config,
        NOMBRE_EJECUCION_VERIFICAR,
        variables,
        usar_cache=usar_cache,
        al_cache=al_cache,
        al_instalar=al_instalar,
    )
    with entorno_docker as entorno:
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
    nombre: str,
    descripcion: str,
    funcion: Callable[..., str],
    con_ignorar_advertencias: bool = False,
) -> Herramienta:
    propiedades: dict[str, object] = {
        "contenido": {"type": "string", "description": "Contenido completo."},
    }
    if con_ignorar_advertencias:
        propiedades["ignorar_advertencias"] = {
            "type": "boolean",
            "description": "Guardar aunque haya advertencias de límites (solo con un motivo).",
        }
    return Herramienta(
        nombre=nombre,
        descripcion=descripcion,
        parametros={"type": "object", "properties": propiedades, "required": ["contenido"]},
        funcion=funcion,
    )


def advertencias_de_limites(config: ConfigProyecto) -> list[str]:
    """Límites por debajo de lo recomendado para el stack (Gradle o Maven) del harness.yaml.

    Pasó en library-api: init escribió 600 s para Gradle (la primera instalación tardó 11
    minutos sobre una carpeta montada desde Windows) y agregó --no-daemon.
    """
    comandos = [*config.docker.instalar, config.comandos.tests, config.comandos.lint or ""]
    texto = " ".join([config.docker.imagen, *comandos]).lower()
    if "gradle" in texto:
        stack, minimos = "Gradle", MINIMOS_GRADLE
    elif "mvn" in texto or "maven" in texto:
        stack, minimos = "Maven", MINIMOS_MAVEN
    else:
        return []
    limites = config.limites
    advertencias = []
    if limites.timeout_comando_segundos < minimos.timeout_comando_segundos:
        advertencias.append(
            f"limites.timeout_comando_segundos es {limites.timeout_comando_segundos}: en "
            f"{stack} se recomienda {minimos.timeout_comando_segundos} (la primera instalación "
            "descarga muchas dependencias)."
        )
    if limites.max_caracteres_salida < minimos.max_caracteres_salida:
        advertencias.append(
            f"limites.max_caracteres_salida es {limites.max_caracteres_salida}: en {stack} se "
            f"recomienda {minimos.max_caracteres_salida} (sus errores son largos)."
        )
    if stack == "Gradle" and any("--no-daemon" in comando for comando in comandos):
        advertencias.append(
            "--no-daemon hace más lento cada comando: el contenedor vive toda la ejecución y "
            "el daemon de Gradle se reutiliza entre comandos. Quítalo."
        )
    return advertencias


def sugerencia_test_logging(repo: Path) -> tuple[str, str] | None:
    """(archivo, bloque) si el build de Gradle no muestra el detalle de las fallas de tests.

    Sin exceptionFormat FULL, Gradle muestra "AssertionError at Archivo.java:49" sin el valor
    esperado ni el obtenido, y el modelo corrige a ciegas (LIB-2 tardó dos intentos).
    """
    for nombre, bloque in BLOQUES_TEST_LOGGING.items():
        ruta = repo / nombre
        if ruta.is_file():
            if "exceptionformat" in ruta.read_text(encoding="utf-8-sig", errors="replace").lower():
                return None
            return nombre, bloque
    return None


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
