"""Caché de dependencias: un volumen de Docker por proyecto que sobrevive entre ejecuciones.

El contenedor del proyecto se crea de cero en cada ejecución, así que lo que descargaban pip,
Maven o Gradle se perdía al borrarlo. El volumen se monta en RUTA_CACHE (fuera del repo) y
cada herramienta se redirige a su subcarpeta con su propia variable de entorno: así no importa
el usuario ni el HOME de la imagen, y la variable de una herramienta que el proyecto no usa no
hace nada (no hace falta adivinar el stack).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from harness.config_proyecto import RUTA_CACHE
from harness.etiquetas import ETIQUETA_PROYECTO  # noqa: F401 - se sigue exportando desde aquí

PREFIJO_VOLUMEN = "harness-cache-"
ETIQUETA_CACHE = "harness.cache"
SUBCARPETAS = ("pip", "uv", "npm", "maven", "gradle")
VARIABLES_CACHE = {
    "PIP_CACHE_DIR": f"{RUTA_CACHE}/pip",
    "UV_CACHE_DIR": f"{RUTA_CACHE}/uv",
    # El volumen es otro sistema de archivos: sin esto uv avisa que no puede usar hardlinks.
    "UV_LINK_MODE": "copy",
    "npm_config_cache": f"{RUTA_CACHE}/npm",
    # Guarda las dependencias y también la distribución del wrapper de Gradle.
    "GRADLE_USER_HOME": f"{RUTA_CACHE}/gradle",
}
# Maven no tiene una variable para el repositorio local: va como propiedad en MAVEN_OPTS
# (solo el repositorio; settings.xml queda donde estaba).
VARIABLE_MAVEN = "MAVEN_OPTS"
PROPIEDAD_MAVEN = "maven.repo.local"
OPCION_MAVEN = f"-D{PROPIEDAD_MAVEN}={RUTA_CACHE}/maven"
# Gradle ejecuta en cada build lo que encuentre aquí: un comando del modelo podría dejarlo en
# la caché y correría en todas las ejecuciones siguientes. Se borra antes de instalar.
CONFIG_EJECUTABLE_GRADLE = (f"{RUTA_CACHE}/gradle/init.d", f"{RUTA_CACHE}/gradle/gradle.properties")
COMANDO_USUARIO = 'echo "$(id -u):$(id -g)"'
PATRON_USUARIO = re.compile(r"\d+:\d+")
MOTIVO_DESACTIVADA = "docker.cache: false en harness.yaml"


@dataclass(frozen=True)
class EstadoCache:
    """Si la ejecución usa la caché, con qué volumen, y si no, por qué."""

    volumen: str | None = None
    es_creado: bool = False
    motivo: str | None = None
    # Variables de caché que el proyecto ya define en variables_entorno: se respetan las suyas.
    variables_respetadas: tuple[str, ...] = ()

    @property
    def es_usada(self) -> bool:
        return self.volumen is not None


def nombre_volumen(proyecto: str) -> str:
    """El volumen de la caché del proyecto: harness-cache-<proyecto>."""
    return f"{PREFIJO_VOLUMEN}{proyecto}"


def variables_cache(variables_proyecto: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """Las variables que redirigen cada herramienta a la caché, y las que se respetan.

    Si el proyecto ya define una, no se pisa. MAVEN_OPTS se combina: la opción del repositorio
    local se agrega al final de la del proyecto, salvo que ya lo defina.
    """
    variables: dict[str, str] = {}
    respetadas: list[str] = []
    for nombre, valor in VARIABLES_CACHE.items():
        if nombre in variables_proyecto:
            respetadas.append(nombre)
        else:
            variables[nombre] = valor
    opciones = variables_proyecto.get(VARIABLE_MAVEN)
    if opciones is None:
        variables[VARIABLE_MAVEN] = OPCION_MAVEN
    elif PROPIEDAD_MAVEN in opciones:
        respetadas.append(VARIABLE_MAVEN)
    else:
        variables[VARIABLE_MAVEN] = f"{opciones} {OPCION_MAVEN}".strip()
    return variables, respetadas


def es_usuario_valido(usuario: str) -> bool:
    """True si es "uid:gid" numérico (sale del contenedor: va a un comando de root)."""
    return PATRON_USUARIO.fullmatch(usuario) is not None


def comando_preparar(usuario: str) -> str:
    """Comando de root que borra la configuración ejecutable de Gradle y deja las subcarpetas
    de la caché a nombre del usuario de la imagen (un volumen nuevo es de root).

    Sin chown -R: lo que se cree después ya es de ese usuario.
    """
    carpetas = " ".join(f"{RUTA_CACHE}/{carpeta}" for carpeta in SUBCARPETAS)
    return (
        f"rm -rf {' '.join(CONFIG_EJECUTABLE_GRADLE)} && mkdir -p {carpetas} && "
        f"chown {usuario} {RUTA_CACHE} {carpetas}"
    )
