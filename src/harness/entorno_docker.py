"""Contenedores Docker donde corre el código del proyecto objetivo.

El harness corre en la máquina del usuario; todo lo que ejecuta código del proyecto (instalar,
tests, comandos del modelo) pasa por aquí y corre dentro de un contenedor. Si el proyecto
necesita servicios (base de datos...), cada uno corre en su contenedor, en una red privada.
"""

import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol

from harness.config_proyecto import NOMBRE_ARCHIVO, ConfigProyecto, ConfigServicio
from harness.globs import coincide_alguno
from harness.secretos import buscar_archivos_de_secretos
from harness.variables import VariablesResueltas, resolver_variables

# La instalación descarga dependencias: puede tardar mucho más que un comando normal.
TIMEOUT_INSTALACION_SEGUNDOS = 900
# Una imagen como postgres:16 pesa cientos de MB: la primera descarga puede tardar.
TIMEOUT_DESCARGA_SEGUNDOS = 600
TIMEOUT_DOCKER_SEGUNDOS = 60
# Margen para que el timeout de dentro del contenedor actúe antes que el del host.
MARGEN_TIMEOUT_HOST_SEGUNDOS = 10
INTERVALO_LISTO_SEGUNDOS = 1.0
# Al fallar un servicio, la causa real suele estar en sus logs (variable mal puesta...).
LINEAS_LOGS_SERVICIO = 20
CODIGO_MATADO = 137
CODIGO_TIEMPO_AGOTADO = -1
MAX_CARACTERES_ERROR_INSTALACION = 3000
# Carpetas y archivos del repo que el contenedor solo puede leer.
MONTAJES_SOLO_LECTURA = (".git", NOMBRE_ARCHIVO, ".harness")
# -v borra también los volúmenes anónimos: imágenes como postgres declaran un VOLUME y Docker
# crea uno por contenedor que, sin -v, queda huérfano ocupando disco.
BORRAR_CONTENEDORES = ["docker", "rm", "-f", "-v"]


class EjecutorProceso(Protocol):
    """Ejecuta un proceso del host; `variables` se suman al entorno del proceso."""

    def __call__(
        self,
        argumentos: list[str],
        timeout: float,
        variables: Mapping[str, str] | None = None,
    ) -> tuple[int, str]: ...


class ErrorDocker(Exception):
    """Docker no está disponible o el contenedor no se pudo preparar."""


@dataclass
class ResultadoComando:
    """Resultado de un comando ejecutado dentro del contenedor."""

    codigo: int
    salida: str
    es_tiempo_agotado: bool = False


def ejecutar_proceso(
    argumentos: list[str], timeout: float, variables: Mapping[str, str] | None = None
) -> tuple[int, str]:
    """Ejecuta un proceso del host y devuelve (código, salida con stdout y stderr juntos)."""
    try:
        proceso = subprocess.run(
            argumentos,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, **variables} if variables else None,
        )
    except subprocess.TimeoutExpired:
        return CODIGO_TIEMPO_AGOTADO, ""
    except FileNotFoundError as error:
        raise ErrorDocker("No se encontró el comando 'docker': instala Docker Desktop.") from error
    return proceso.returncode, proceso.stdout


class EntornoDocker:
    """Contenedor del proyecto y de sus servicios, en una red privada, con limpieza garantizada.

    Uso: `with EntornoDocker(...) as entorno: entorno.iniciar(); entorno.ejecutar(...)`.
    """

    def __init__(
        self,
        repo: Path,
        config: ConfigProyecto,
        ticket: str,
        variables: VariablesResueltas | None = None,
        ejecutar: EjecutorProceso = ejecutar_proceso,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self._repo = repo.resolve()
        self._config = config
        self._variables = variables or resolver_variables(config)
        self._ejecutar = ejecutar
        self._reloj = reloj
        self._dormir = dormir
        # El contenedor del proyecto y la red comparten nombre (Docker los separa por tipo).
        self.nombre = f"harness-{config.proyecto}-{_nombre_seguro(ticket)}"
        self._contenedores: list[str] = []
        self._red: str | None = None
        # Archivo vacío del host que se monta encima de cada .env del repo.
        self._archivo_vacio: Path | None = None
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

    def iniciar(self, al_avanzar: Callable[[str], None] | None = None) -> None:
        """Levanta red y servicios, crea el contenedor del proyecto e instala dependencias."""
        avisar = al_avanzar or (lambda _: None)
        self._verificar_docker()
        self._limpiar_restos()
        for imagen in self._imagenes():
            self._descargar_si_falta(imagen, avisar)
        servicios = self._config.servicios
        if servicios:
            self._crear_red()
        for servicio in servicios:
            avisar(f"Levantando servicio {servicio.nombre} ({servicio.imagen})...")
            self._levantar_servicio(servicio)
        # Se esperan después de levantarlos todos, así arrancan en paralelo.
        for servicio in servicios:
            if servicio.listo_cuando:
                avisar(f"Esperando a que el servicio {servicio.nombre} esté listo...")
                self._esperar_listo(servicio)
        avisar(f"Creando el contenedor del proyecto ({self._config.docker.imagen})...")
        self._correr(self.nombre, self._argumentos_proyecto(), self._variables.proyecto)
        avisar("Instalando dependencias...")
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
        """Borra los contenedores y la red creados; nunca lanza excepciones."""
        try:
            if self._contenedores:
                self._ejecutar(
                    [*BORRAR_CONTENEDORES, *reversed(self._contenedores)], TIMEOUT_DOCKER_SEGUNDOS
                )
            # La red se borra al final: Docker no la borra mientras tenga contenedores.
            if self._red:
                self._ejecutar(["docker", "network", "rm", self._red], TIMEOUT_DOCKER_SEGUNDOS)
        except ErrorDocker:
            pass  # Docker desapareció: no hay nada más que limpiar desde aquí.
        self._contenedores.clear()
        self._red = None
        if self._archivo_vacio:
            # Después de borrar los contenedores: mientras existan, el archivo está montado.
            self._archivo_vacio.unlink(missing_ok=True)
            self._archivo_vacio = None

    def _verificar_docker(self) -> None:
        argumentos = ["docker", "version", "--format", "{{.Server.Version}}"]
        codigo, _ = self._ejecutar(argumentos, TIMEOUT_DOCKER_SEGUNDOS)
        if codigo != 0:
            raise ErrorDocker("Docker no está corriendo: abre Docker Desktop y vuelve a intentar.")

    def _nombre_servicio(self, servicio: ConfigServicio) -> str:
        return f"{self.nombre}-{servicio.nombre}"

    def _limpiar_restos(self) -> None:
        # Si una ejecución anterior se cortó sin limpiar, sus contenedores ocupan los nombres.
        nombres = [self.nombre, *(self._nombre_servicio(s) for s in self._config.servicios)]
        self._ejecutar([*BORRAR_CONTENEDORES, *nombres], TIMEOUT_DOCKER_SEGUNDOS)
        if self._config.servicios:
            self._ejecutar(["docker", "network", "rm", self.nombre], TIMEOUT_DOCKER_SEGUNDOS)

    def _imagenes(self) -> list[str]:
        imagenes = [servicio.imagen for servicio in self._config.servicios]
        imagenes.append(self._config.docker.imagen)
        return list(dict.fromkeys(imagenes))

    def _descargar_si_falta(self, imagen: str, avisar: Callable[[str], None]) -> None:
        # docker run también descarga, pero con el timeout corto de un comando de Docker.
        codigo, _ = self._ejecutar(["docker", "image", "inspect", imagen], TIMEOUT_DOCKER_SEGUNDOS)
        if codigo == 0:
            return
        avisar(f"Descargando la imagen {imagen} (solo la primera vez)...")
        codigo, salida = self._ejecutar(["docker", "pull", imagen], TIMEOUT_DESCARGA_SEGUNDOS)
        if codigo == CODIGO_TIEMPO_AGOTADO:
            raise ErrorDocker(
                f"La descarga de la imagen '{imagen}' tardó más de {TIMEOUT_DESCARGA_SEGUNDOS} s."
            )
        if codigo != 0:
            raise ErrorDocker(f"No se pudo descargar la imagen '{imagen}': {salida.strip()}")

    def _crear_red(self) -> None:
        self._red = self.nombre
        codigo, salida = self._ejecutar(
            ["docker", "network", "create", "--label", "harness=1", self._red],
            TIMEOUT_DOCKER_SEGUNDOS,
        )
        if codigo != 0:
            raise ErrorDocker(f"No se pudo crear la red de Docker: {salida.strip()}")

    def _levantar_servicio(self, servicio: ConfigServicio) -> None:
        nombre = self._nombre_servicio(servicio)
        variables = self._variables.servicios.get(servicio.nombre, {})
        # El nombre del contenedor es global en Docker; el alias es la dirección dentro de
        # la red ('db'), así dos ejecuciones pueden tener cada una su propio 'db'.
        argumentos = [
            "docker", "run", "-d", "--name", nombre, "--label", "harness=1",
            "--network", self.nombre, "--network-alias", servicio.nombre,
            *_argumentos_variables(variables), servicio.imagen,
        ]
        self._correr(nombre, argumentos, variables)

    def _correr(self, nombre: str, argumentos: list[str], variables: Mapping[str, str]) -> None:
        # Se anota antes de crearlo: si docker run falla a medias, cerrar() lo borra igual.
        self._contenedores.append(nombre)
        codigo, salida = self._ejecutar(argumentos, TIMEOUT_DOCKER_SEGUNDOS, variables)
        if codigo != 0:
            raise ErrorDocker(f"No se pudo crear el contenedor '{nombre}': {salida.strip()}")

    def _esperar_listo(self, servicio: ConfigServicio) -> None:
        nombre = self._nombre_servicio(servicio)
        comprobacion = ["docker", "exec", nombre, "sh", "-c", servicio.listo_cuando or "true"]
        limite = self._reloj() + servicio.timeout_listo_segundos
        while True:
            codigo, salida = self._ejecutar(comprobacion, TIMEOUT_DOCKER_SEGUNDOS)
            if codigo == 0:
                return
            # Un servicio que se detuvo (por ejemplo, postgres sin contraseña) no va a estar
            # listo nunca: no tiene sentido esperar el timeout completo.
            if not self._esta_corriendo(nombre):
                motivo = "se detuvo antes de estar listo"
                raise ErrorDocker(self._mensaje_no_listo(servicio, motivo, salida))
            if self._reloj() >= limite:
                motivo = f"no estuvo listo en {servicio.timeout_listo_segundos} s"
                raise ErrorDocker(self._mensaje_no_listo(servicio, motivo, salida))
            self._dormir(INTERVALO_LISTO_SEGUNDOS)

    def _esta_corriendo(self, contenedor: str) -> bool:
        # Solo un "false" explícito cuenta como detenido: si docker inspect falla por otro
        # motivo, se sigue esperando y decide el timeout.
        _, salida = self._ejecutar(
            ["docker", "inspect", "-f", "{{.State.Running}}", contenedor],
            TIMEOUT_DOCKER_SEGUNDOS,
        )
        return salida.strip() != "false"

    def _mensaje_no_listo(self, servicio: ConfigServicio, motivo: str, salida: str) -> str:
        nombre = self._nombre_servicio(servicio)
        _, logs = self._ejecutar(
            ["docker", "logs", "--tail", str(LINEAS_LOGS_SERVICIO), nombre],
            TIMEOUT_DOCKER_SEGUNDOS,
        )
        return (
            f"El servicio '{servicio.nombre}' {motivo} "
            f"('{servicio.listo_cuando}' no devolvió 0).\n"
            f"Última salida de la comprobación: {salida.strip() or '(vacía)'}\n"
            f"Últimas {LINEAS_LOGS_SERVICIO} líneas de 'docker logs' del servicio:\n"
            f"{logs.strip() or '(sin salida)'}"
        )

    def _argumentos_proyecto(self) -> list[str]:
        destino = self._config.docker.directorio_trabajo
        argumentos = ["docker", "run", "-d", "--name", self.nombre, "--label", "harness=1"]
        if self._red:
            argumentos += ["--network", self._red]
        argumentos += _argumentos_variables(self._variables.proyecto)
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
        argumentos += self._montajes_secretos(destino)
        argumentos += ["-w", destino, self._config.docker.imagen, "sleep", "infinity"]
        return argumentos

    def _montajes_secretos(self, destino: str) -> list[str]:
        """Tapa cada .env con un archivo vacío de solo lectura.

        Las herramientas ya no dejan leerlos, pero el repo está montado completo: sin esto,
        'cat .env' o un test que lo imprima le mostraría los secretos al modelo.
        """
        rutas = buscar_archivos_de_secretos(self._repo)
        if not rutas:
            return []
        if self._archivo_vacio is None:
            descriptor, ruta_vacio = tempfile.mkstemp(prefix="harness-vacio-")
            os.close(descriptor)
            self._archivo_vacio = Path(ruta_vacio)
        argumentos: list[str] = []
        for ruta in rutas:
            argumentos += [
                "--mount",
                f"type=bind,source={self._archivo_vacio},target={destino}/{ruta},readonly",
            ]
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


def _argumentos_variables(variables: Mapping[str, str]) -> list[str]:
    """`-e NOMBRE` sin valor: Docker lo toma del entorno del proceso y no queda a la vista
    en la línea de comandos (que cualquier proceso de la máquina puede leer)."""
    argumentos: list[str] = []
    for nombre in variables:
        argumentos += ["-e", nombre]
    return argumentos


def _nombre_seguro(texto: str) -> str:
    """Docker solo acepta ciertos caracteres en los nombres: 'TAREAS 1' -> 'tareas-1'."""
    return re.sub(r"[^a-z0-9-]+", "-", texto.lower()).strip("-") or "ticket"
