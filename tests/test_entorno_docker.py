from collections.abc import Mapping
from pathlib import Path

import pytest

from harness.config_proyecto import ConfigComandos, ConfigDocker, ConfigProyecto
from harness.entorno_docker import (
    CODIGO_MATADO,
    CODIGO_TIEMPO_AGOTADO,
    EntornoDocker,
    ErrorDocker,
)
from harness.variables import resolver_variables

CONFIG = ConfigProyecto(
    version=1,
    proyecto="tareas-api",
    docker=ConfigDocker(
        imagen="python:3.12-slim",
        instalar=["pip install -e .", "pip install pytest"],
        reinstalar_si_cambia=["pyproject.toml"],
    ),
    comandos=ConfigComandos(tests="pytest -q"),
)


class DockerFalso:
    """Imita el CLI de docker: guarda los argumentos y responde según el subcomando."""

    def __init__(self, respuestas: dict[str, list[tuple[int, str]]] | None = None) -> None:
        self.llamadas: list[list[str]] = []
        self.variables: list[dict[str, str]] = []
        self._respuestas = respuestas or {}

    def __call__(
        self,
        argumentos: list[str],
        timeout: float,
        variables: Mapping[str, str] | None = None,
    ) -> tuple[int, str]:
        self.llamadas.append(argumentos)
        self.variables.append(dict(variables or {}))
        clave = argumentos[1] if argumentos[1] != "exec" else argumentos[-1]
        cola = self._respuestas.get(clave)
        return cola.pop(0) if cola else (0, "ok")

    def subcomandos(self) -> list[str]:
        return [a[1] if a[1] != "exec" else f"exec {a[-1]}" for a in self.llamadas]


def _iniciar(repo: Path, docker: DockerFalso, **kwargs) -> EntornoDocker:
    entorno = EntornoDocker(repo, CONFIG, "TAREAS-1", ejecutar=docker, **kwargs)
    entorno.iniciar()
    return entorno


def test_iniciar_crea_contenedor_e_instala(repo: Path) -> None:
    docker = DockerFalso()
    with _iniciar(repo, docker) as entorno:
        assert entorno.nombre == "harness-tareas-api-tareas-1"
        assert docker.subcomandos() == [
            "version", "rm", "image", "run", "exec pip install -e .", "exec pip install pytest",
        ]
    assert docker.llamadas[-1] == ["docker", "rm", "-f", "-v", "harness-tareas-api-tareas-1"]


def test_montajes_de_solo_lectura(repo: Path) -> None:
    docker = DockerFalso()
    _iniciar(repo, docker).cerrar()
    run = next(a for a in docker.llamadas if a[1] == "run")
    montajes = [run[i + 1] for i, a in enumerate(run) if a == "--mount"]
    raiz = repo.resolve()
    assert montajes[0] == f"type=bind,source={raiz},target=/app"
    assert f"type=bind,source={raiz / '.git'},target=/app/.git,readonly" in montajes
    assert f"type=bind,source={raiz / 'harness.yaml'},target=/app/harness.yaml,readonly" in montajes
    assert f"type=bind,source={raiz / '.harness'},target=/app/.harness,readonly" in montajes
    assert run[-5:] == ["-w", "/app", "python:3.12-slim", "sleep", "infinity"]


def test_ejecutar_usa_timeout_dentro_del_contenedor(repo: Path) -> None:
    docker = DockerFalso()
    with _iniciar(repo, docker) as entorno:
        entorno.ejecutar("pytest -q")
    exec_ = docker.llamadas[-2]
    assert exec_ == [
        "docker", "exec", "harness-tareas-api-tareas-1",
        "timeout", "-s", "KILL", "120", "sh", "-c", "pytest -q",
    ]


def test_tiempo_agotado(repo: Path) -> None:
    # Cada comando lee el reloj al empezar y al terminar; los 2 de instalar duran 0 s.
    tiempos = [0.0, 0.0, 0.0, 0.0, 0.0, 121.0]
    docker = DockerFalso({"sleep-forever": [(CODIGO_MATADO, "")]})
    entorno = _iniciar(repo, docker, reloj=lambda: tiempos.pop(0))
    assert entorno.ejecutar("sleep-forever").es_tiempo_agotado


def test_codigo_137_rapido_no_es_tiempo_agotado(repo: Path) -> None:
    docker = DockerFalso({"python oom.py": [(CODIGO_MATADO, "Killed")]})
    entorno = _iniciar(repo, docker)
    assert not entorno.ejecutar("python oom.py").es_tiempo_agotado


def test_timeout_del_host(repo: Path) -> None:
    docker = DockerFalso({"pytest": [(CODIGO_TIEMPO_AGOTADO, "")]})
    assert _iniciar(repo, docker).ejecutar("pytest").es_tiempo_agotado


def test_docker_no_esta_corriendo(repo: Path) -> None:
    docker = DockerFalso({"version": [(1, "error during connect")]})
    with pytest.raises(ErrorDocker, match="abre Docker Desktop"):
        with EntornoDocker(repo, CONFIG, "T", ejecutar=docker) as entorno:
            entorno.iniciar()
    assert docker.subcomandos() == ["version"]


def test_si_falla_la_instalacion_se_borra_el_contenedor(repo: Path) -> None:
    docker = DockerFalso({"pip install -e .": [(1, "ERROR: no se encontró setup.py")]})
    with pytest.raises(ErrorDocker, match="no se encontró setup.py"):
        with EntornoDocker(repo, CONFIG, "T", ejecutar=docker) as entorno:
            entorno.iniciar()
    assert docker.llamadas[-1][:4] == ["docker", "rm", "-f", "-v"]


def test_ctrl_c_borra_el_contenedor(repo: Path) -> None:
    docker = DockerFalso()
    with pytest.raises(KeyboardInterrupt):
        with EntornoDocker(repo, CONFIG, "T", ejecutar=docker) as entorno:
            entorno.iniciar()
            raise KeyboardInterrupt
    assert docker.subcomandos()[-1] == "rm"


def test_reinstala_si_cambia_un_archivo_de_dependencias(repo: Path) -> None:
    docker = DockerFalso()
    entorno = _iniciar(repo, docker)
    entorno.marcar_cambio("src/app.py")
    entorno.ejecutar("pytest")
    entorno.marcar_cambio("pyproject.toml")
    entorno.ejecutar("pytest")
    entorno.ejecutar("pytest")
    assert docker.subcomandos()[6:] == [
        "exec pytest",
        "exec pip install -e .", "exec pip install pytest", "exec pytest",
        "exec pytest",
    ]


def test_reinstalacion_fallida_vuelve_al_modelo(repo: Path) -> None:
    docker = DockerFalso({"pip install -e .": [(0, ""), (1, "pyproject.toml inválido")]})
    entorno = _iniciar(repo, docker)
    entorno.marcar_cambio("pyproject.toml")
    resultado = entorno.ejecutar("pytest")
    assert resultado.codigo == 1
    assert "pyproject.toml inválido" in resultado.salida


CONFIG_SERVICIOS = ConfigProyecto.model_validate({
    "version": 1,
    "proyecto": "notas-api",
    "docker": {"imagen": "python:3.12-slim", "instalar": "pip install -r requirements.txt"},
    "comandos": {"tests": "pytest -q"},
    "servicios": [{
        "nombre": "db",
        "imagen": "postgres:16",
        "variables": {"POSTGRES_PASSWORD": "${CLAVE_PG}"},
        "listo_cuando": "pg_isready -h 127.0.0.1",
        "timeout_listo_segundos": 30,
    }],
    "variables_entorno": {"DATABASE_URL": "postgresql://postgres:${CLAVE_PG}@db/notas"},
})
CONTENEDOR = "harness-notas-api-notas-1"
SERVICIO = "harness-notas-api-notas-1-db"


def _entorno_servicios(repo: Path, docker: DockerFalso, **kwargs) -> EntornoDocker:
    variables = resolver_variables(CONFIG_SERVICIOS, {"CLAVE_PG": "secreta"})
    return EntornoDocker(repo, CONFIG_SERVICIOS, "NOTAS-1", variables, ejecutar=docker, **kwargs)


def _sin_dormir(segundos: float) -> None:
    pass


def test_servicios_en_red_privada_antes_que_el_proyecto(repo: Path) -> None:
    docker = DockerFalso()
    avisos: list[str] = []
    with _entorno_servicios(repo, docker) as entorno:
        entorno.iniciar(al_avanzar=avisos.append)
    assert docker.subcomandos() == [
        "version", "rm", "network", "image", "image", "network", "run",
        "exec pg_isready -h 127.0.0.1", "run", "exec pip install -r requirements.txt",
        "rm", "network",
    ]
    assert docker.llamadas[1] == ["docker", "rm", "-f", "-v", CONTENEDOR, SERVICIO]
    assert docker.llamadas[5] == [
        "docker", "network", "create", "--label", "harness=1", CONTENEDOR,
    ]
    assert docker.llamadas[-2] == ["docker", "rm", "-f", "-v", CONTENEDOR, SERVICIO]
    assert docker.llamadas[-1] == ["docker", "network", "rm", CONTENEDOR]
    assert "Esperando a que el servicio db esté listo..." in avisos


def test_borra_los_volumenes_anonimos_de_los_contenedores(repo: Path) -> None:
    # postgres declara un VOLUME: sin -v, cada ejecución dejaría un volumen huérfano.
    docker = DockerFalso()
    with _entorno_servicios(repo, docker) as entorno:
        entorno.iniciar()
    borrados = [a for a in docker.llamadas if a[1] == "rm"]
    assert len(borrados) == 2  # restos al empezar y limpieza al cerrar
    assert all(a[2:4] == ["-f", "-v"] for a in borrados)


def test_argumentos_del_servicio_y_del_proyecto(repo: Path) -> None:
    docker = DockerFalso()
    _entorno_servicios(repo, docker).iniciar()
    servicio, proyecto = [a for a in docker.llamadas if a[1] == "run"]
    assert servicio == [
        "docker", "run", "-d", "--name", SERVICIO, "--label", "harness=1",
        "--network", CONTENEDOR, "--network-alias", "db",
        "-e", "POSTGRES_PASSWORD", "postgres:16",
    ]
    assert proyecto[7:11] == ["--network", CONTENEDOR, "-e", "DATABASE_URL"]


def test_los_valores_de_las_variables_no_van_en_la_linea_de_comandos(repo: Path) -> None:
    docker = DockerFalso()
    _entorno_servicios(repo, docker).iniciar()
    assert not any("secreta" in argumento for a in docker.llamadas for argumento in a)
    indices = [i for i, a in enumerate(docker.llamadas) if a[1] == "run"]
    assert docker.variables[indices[0]] == {"POSTGRES_PASSWORD": "secreta"}
    assert docker.variables[indices[1]] == {
        "DATABASE_URL": "postgresql://postgres:secreta@db/notas",
    }


def test_espera_hasta_que_el_servicio_responde(repo: Path) -> None:
    docker = DockerFalso({"pg_isready -h 127.0.0.1": [(2, "no response"), (2, ""), (0, "")]})
    esperas: list[float] = []
    _entorno_servicios(repo, docker, dormir=esperas.append).iniciar()
    assert docker.subcomandos().count("exec pg_isready -h 127.0.0.1") == 3
    assert esperas == [1.0, 1.0]


def test_servicio_que_no_esta_listo_muestra_sus_logs(repo: Path) -> None:
    respuestas = {
        "pg_isready -h 127.0.0.1": [(2, "127.0.0.1:5432 - no response")] * 100,
        "logs": [(0, "Error: Database is uninitialized and superuser password is not specified.")],
    }
    docker = DockerFalso(respuestas)
    reloj = iter(range(0, 1000, 10))
    with pytest.raises(ErrorDocker) as error:
        with _entorno_servicios(
            repo, docker, reloj=lambda: next(reloj), dormir=_sin_dormir
        ) as entorno:
            entorno.iniciar()
    mensaje = str(error.value)
    assert "El servicio 'db' no estuvo listo en 30 s" in mensaje
    assert "127.0.0.1:5432 - no response" in mensaje
    assert "superuser password is not specified" in mensaje
    assert ["docker", "logs", "--tail", "20", SERVICIO] in docker.llamadas
    # El proyecto no llegó a crearse, pero el servicio y la red sí se borran.
    assert docker.llamadas[-2] == ["docker", "rm", "-f", "-v", SERVICIO]
    assert docker.llamadas[-1] == ["docker", "network", "rm", CONTENEDOR]


def test_descarga_solo_las_imagenes_que_faltan(repo: Path) -> None:
    docker = DockerFalso({"image": [(1, "No such image: postgres:16"), (0, "[]")]})
    avisos: list[str] = []
    _entorno_servicios(repo, docker).iniciar(al_avanzar=avisos.append)
    descargas = [a for a in docker.llamadas if a[1] == "pull"]
    assert descargas == [["docker", "pull", "postgres:16"]]
    assert "Descargando la imagen postgres:16 (solo la primera vez)..." in avisos


def test_descarga_fallida(repo: Path) -> None:
    docker = DockerFalso({"image": [(1, "")], "pull": [(1, "manifest unknown")]})
    with pytest.raises(ErrorDocker, match="No se pudo descargar la imagen 'postgres:16'"):
        _entorno_servicios(repo, docker).iniciar()


def test_red_que_no_se_puede_crear(repo: Path) -> None:
    docker = DockerFalso({"network": [(1, ""), (1, "network exists")]})
    with pytest.raises(ErrorDocker, match="No se pudo crear la red"):
        with _entorno_servicios(repo, docker) as entorno:
            entorno.iniciar()
    assert docker.llamadas[-1] == ["docker", "network", "rm", CONTENEDOR]


def test_sin_servicios_no_se_crea_red(repo: Path) -> None:
    docker = DockerFalso()
    with _iniciar(repo, docker):
        pass
    assert "network" not in docker.subcomandos()
    run = next(a for a in docker.llamadas if a[1] == "run")
    assert "--network" not in run


def _montajes(docker: DockerFalso) -> list[str]:
    run = next(a for a in docker.llamadas if a[1] == "run")
    return [run[i + 1] for i, a in enumerate(run) if a == "--mount"]


def test_tapa_los_env_con_un_archivo_vacio_de_solo_lectura(repo: Path) -> None:
    for ruta in [".env", "backend/.env.local", ".env.example", ".venv/.env"]:
        (repo / ruta).parent.mkdir(parents=True, exist_ok=True)
        (repo / ruta).write_text("API_KEY=real\n", encoding="utf-8")
    docker = DockerFalso()
    entorno = _iniciar(repo, docker)

    tapas = [m for m in _montajes(docker) if ".env" in m]
    fuentes = {m.split(",")[1].removeprefix("source=") for m in tapas}
    assert len(fuentes) == 1
    vacio = Path(fuentes.pop())
    assert tapas == [
        f"type=bind,source={vacio},target=/app/.env,readonly",
        f"type=bind,source={vacio},target=/app/backend/.env.local,readonly",
    ]
    assert vacio.read_bytes() == b""
    # El .env del disco no se toca.
    assert (repo / ".env").read_text(encoding="utf-8") == "API_KEY=real\n"

    entorno.cerrar()
    assert not vacio.exists()


def test_sin_env_no_hay_montajes_extra(repo: Path) -> None:
    docker = DockerFalso()
    _iniciar(repo, docker).cerrar()
    assert not any(".env" in m for m in _montajes(docker))
