"""Contenedores Docker donde corre el código del proyecto objetivo.

El harness corre en la máquina del usuario; todo lo que ejecuta código del proyecto (instalar,
tests, comandos del modelo) pasa por aquí y corre dentro de un contenedor.
"""

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from harness.config_proyecto import NOMBRE_ARCHIVO, ConfigProyecto
from harness.globs import coincide_alguno

# La instalación descarga dependencias: puede tardar mucho más que un comando normal.
TIMEOUT_INSTALACION_SEGUNDOS = 900
TIMEOUT_DOCKER_SEGUNDOS = 60
# Margen para que el timeout de dentro del contenedor actúe antes que el del host.
MARGEN_TIMEOUT_HOST_SEGUNDOS = 10
CODIGO_MATADO = 137
CODIGO_TIEMPO_AGOTADO = -1
MAX_CARACTERES_ERROR_INSTALACION = 3000
# Carpetas y archivos del repo que el contenedor solo puede leer.
MONTAJES_SOLO_LECTURA = (".git", NOMBRE_ARCHIVO, ".harness")

EjecutorProceso = Callable[[list[str], float], tuple[int, str]]


class ErrorDocker(Exception):
    """Docker no está disponible o el contenedor no se pudo preparar."""


@dataclass
class ResultadoComando:
    """Resultado de un comando ejecutado dentro del contenedor."""

    codigo: int
    salida: str
    es_tiempo_agotado: bool = False


def ejecutar_proceso(argumentos: list[str], timeout: float) -> tuple[int, str]:
    """Ejecuta un proceso del host y devuelve (código, salida con stdout y stderr juntos)."""
    try:
        proceso = subprocess.run(
            argumentos,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CODIGO_TIEMPO_AGOTADO, ""
    except FileNotFoundError as error:
        raise ErrorDocker("No se encontró el comando 'docker': instala Docker Desktop.") from error
    return proceso.returncode, proceso.stdout


class EntornoDocker:
    """Contenedor del proyecto (y desde la fase 3, sus servicios), con limpieza garantizada.

    Uso: `with EntornoDocker(...) as entorno: entorno.iniciar(); entorno.ejecutar(...)`.
    """

    def __init__(
        self,
        repo: Path,
        config: ConfigProyecto,
        ticket: str,
        ejecutar: EjecutorProceso = ejecutar_proceso,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repo.resolve()
        self._config = config
        self._ejecutar = ejecutar
        self._reloj = reloj
        self.nombre = f"harness-{config.proyecto}-{_nombre_seguro(ticket)}"
        # Lista desde ya: en la fase 3 se suman los contenedores de los servicios.
        self._contenedores: list[str] = []
        self._hay_que_reinstalar = False

    def __enter__(self) -> "EntornoDocker":
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        error: BaseException | None,
        traza: TracebackType | None,
    ) -> None:
        self.cerrar()

    def iniciar(self) -> None:
        """Comprueba Docker, crea el contenedor del proyecto e instala dependencias."""
        self._verificar_docker()
        # Si una ejecución anterior se cortó sin limpiar, su contenedor ocupa el nombre.
        self._ejecutar(["docker", "rm", "-f", self.nombre], TIMEOUT_DOCKER_SEGUNDOS)
        codigo, salida = self._ejecutar(self._argumentos_run(), TIMEOUT_DOCKER_SEGUNDOS)
        if codigo != 0:
            raise ErrorDocker(f"No se pudo crear el contenedor: {salida.strip()}")
        self._contenedores.append(self.nombre)
        self._instalar()

    def ejecutar(self, comando: str, timeout: int | None = None) -> ResultadoComando:
        """Ejecuta un comando con `sh -c` en el contenedor del proyecto, con timeout."""
        if self._hay_que_reinstalar:
            self._hay_que_reinstalar = False
            try:
                self._instalar()
            except ErrorDocker as error:
                return ResultadoComando(1, f"Falló la reinstalación de dependencias:\n{error}")
        return self._exec(comando, timeout or self._config.limites.timeout_comando_segundos)

    def marcar_cambio(self, ruta: str) -> None:
        """Avisa que se escribió un archivo; si es de dependencias, se reinstalará."""
        if coincide_alguno(ruta, self._config.docker.reinstalar_si_cambia):
            self._hay_que_reinstalar = True

    def cerrar(self) -> None:
        """Borra todos los contenedores creados; nunca lanza excepciones."""
        for nombre in reversed(self._contenedores):
            try:
                self._ejecutar(["docker", "rm", "-f", nombre], TIMEOUT_DOCKER_SEGUNDOS)
            except ErrorDocker:
                pass  # Docker desapareció: no hay nada más que limpiar desde aquí.
        self._contenedores.clear()

    def _verificar_docker(self) -> None:
        argumentos = ["docker", "version", "--format", "{{.Server.Version}}"]
        codigo, _ = self._ejecutar(argumentos, TIMEOUT_DOCKER_SEGUNDOS)
        if codigo != 0:
            raise ErrorDocker("Docker no está corriendo: abre Docker Desktop y vuelve a intentar.")

    def _argumentos_run(self) -> list[str]:
        destino = self._config.docker.directorio_trabajo
        argumentos = ["docker", "run", "-d", "--name", self.nombre, "--label", "harness=1"]
        argumentos += ["--mount", f"type=bind,source={self._repo},target={destino}"]
        for ruta in MONTAJES_SOLO_LECTURA:
            origen = self._repo / ruta
            if ruta == ".harness":
                origen.mkdir(exist_ok=True)
            if origen.exists():
                # Desde el contenedor no se puede tocar .git (un hook escrito ahí correría en
                # la máquina del usuario al hacer commit), harness.yaml ni logs/sesiones.
                argumentos += [
                    "--mount",
                    f"type=bind,source={origen},target={destino}/{ruta},readonly",
                ]
        argumentos += ["-w", destino, self._config.docker.imagen, "sleep", "infinity"]
        return argumentos

    def _instalar(self) -> None:
        for comando in self._config.docker.instalar:
            resultado = self._exec(comando, TIMEOUT_INSTALACION_SEGUNDOS)
            if resultado.codigo != 0:
                detalle = resultado.salida[-MAX_CARACTERES_ERROR_INSTALACION:].strip()
                motivo = "tiempo agotado" if resultado.es_tiempo_agotado else detalle
                raise ErrorDocker(f"Falló la instalación '{comando}': {motivo}")

    def _exec(self, comando: str, timeout: int) -> ResultadoComando:
        # timeout -s KILL corta el proceso dentro del contenedor; el timeout del host solo
        # cortaría el cliente 'docker exec' y dejaría el proceso corriendo.
        argumentos = [
            "docker", "exec", self.nombre,
            "timeout", "-s", "KILL", str(timeout), "sh", "-c", comando,
        ]
        inicio = self._reloj()
        codigo, salida = self._ejecutar(argumentos, timeout + MARGEN_TIMEOUT_HOST_SEGUNDOS)
        transcurrido = self._reloj() - inicio
        es_tiempo_agotado = codigo == CODIGO_TIEMPO_AGOTADO or (
            codigo == CODIGO_MATADO and transcurrido >= timeout
        )
        return ResultadoComando(codigo, salida, es_tiempo_agotado)


def _nombre_seguro(texto: str) -> str:
    """Docker solo acepta ciertos caracteres en los nombres: 'TAREAS 1' -> 'tareas-1'."""
    return re.sub(r"[^a-z0-9-]+", "-", texto.lower()).strip("-") or "ticket"
