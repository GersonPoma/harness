from pathlib import Path

import pytest

from harness.config_proyecto import ConfigComandos, ConfigDocker, ConfigProyecto
from harness.entorno_docker import (
    CODIGO_MATADO,
    CODIGO_TIEMPO_AGOTADO,
    EntornoDocker,
    ErrorDocker,
)

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
        self._respuestas = respuestas or {}

    def __call__(self, argumentos: list[str], timeout: float) -> tuple[int, str]:
        self.llamadas.append(argumentos)
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
            "version", "rm", "run", "exec pip install -e .", "exec pip install pytest",
        ]
    assert docker.llamadas[-1] == ["docker", "rm", "-f", "harness-tareas-api-tareas-1"]


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
    assert docker.llamadas[-1][:3] == ["docker", "rm", "-f"]


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
    assert docker.subcomandos()[5:] == [
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
