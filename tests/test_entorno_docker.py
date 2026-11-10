import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from harness.cache_dependencias import (
    COMANDO_USUARIO,
    EstadoCache,
    comando_preparar,
    variables_cache,
)
from harness.config_proyecto import ConfigComandos, ConfigDocker, ConfigProyecto
from harness.entorno_docker import (
    CODIGO_MATADO,
    CODIGO_TIEMPO_AGOTADO,
    COMANDO_TIMEOUT,
    MENSAJE_DOCKER_NO_CORRE,
    Conservados,
    EntornoDocker,
    ErrorDocker,
    Instalacion,
    ejecutar_proceso_con_lineas,
    verificar_docker,
)
from harness import entorno_docker
from harness import etiquetas as modulo_etiquetas
from harness.variables import resolver_variables

# El proceso del harness en los tests: pid 100 en la máquina "pc" (y está vivo).
PID_VIVO = 100
HOST = "pc"
ID = "e1"


@pytest.fixture(autouse=True)
def proceso_fijo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Etiquetas que no dependen de quién corre los tests; solo el pid 100 está vivo."""
    proceso = {"harness": "1", "harness.pid": str(PID_VIVO), "harness.host": HOST}
    monkeypatch.setattr(modulo_etiquetas, "host_actual", lambda: HOST)
    monkeypatch.setattr(modulo_etiquetas, "etiquetas_del_proceso", lambda: dict(proceso))
    monkeypatch.setattr(modulo_etiquetas, "proceso_vivo", lambda pid: pid == PID_VIVO)


def _etiquetas(proyecto: str, ticket: str) -> list[str]:
    return [
        "--label", "harness=1", "--label", f"harness.pid={PID_VIVO}",
        "--label", f"harness.host={HOST}", "--label", f"harness.proyecto={proyecto}",
        "--label", f"harness.ticket={ticket}", "--label", f"harness.ejecucion={ID}",
    ]


def _inspeccion(*contenedores: dict) -> str:
    """Lo que devuelve 'docker inspect' de cada contenedor (solo los campos que se leen)."""
    datos = [
        {
            "Id": c["id"], "Name": f"/{c['nombre']}", "Config": {"Labels": c["etiquetas"]},
            "State": {"Running": c.get("corriendo", True)}, "Mounts": [],
        }
        for c in contenedores
    ]
    return json.dumps(datos)


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


# Sin otra ejecución viva del proyecto, y la imagen corre como root.
RESPUESTAS_POR_DEFECTO = {
    "ps": (0, ""), "network ls": (0, ""), "volume ls": (0, ""), COMANDO_USUARIO: (0, "0:0\n"),
}
# Lo que corre antes de instalar cuando se usa la caché (el volumen ya existe).
PREPARAR_CACHE = ["ps", "volume"]
PREPARAR_VOLUMEN = [f"exec {COMANDO_USUARIO}", f"exec {comando_preparar('0:0')}"]
# Después de crear el contenedor del proyecto: ¿tiene sh y timeout?
REVISAR_TIMEOUT = f"exec {COMANDO_TIMEOUT}"


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
        if clave in ("network", "volume") and argumentos[2] == "ls":
            # Listar restos por etiqueta: por defecto no hay ninguno.
            clave = f"{clave} ls"
        cola = self._respuestas.get(clave)
        return cola.pop(0) if cola else RESPUESTAS_POR_DEFECTO.get(clave, (0, "ok"))

    def subcomandos(self) -> list[str]:
        return [a[1] if a[1] != "exec" else f"exec {a[-1]}" for a in self.llamadas]


def _iniciar(repo: Path, docker: DockerFalso, **kwargs) -> EntornoDocker:
    entorno = EntornoDocker(repo, CONFIG, "TAREAS-1", ejecutar=docker, id_ejecucion=ID, **kwargs)
    entorno.iniciar()
    return entorno


NOMBRE = "harness-tareas-api-tareas-1-e1"


def test_iniciar_crea_contenedor_e_instala(repo: Path) -> None:
    docker = DockerFalso()
    with _iniciar(repo, docker) as entorno:
        assert entorno.nombre == NOMBRE
        assert docker.subcomandos() == [
            "version", "ps", "image", *PREPARAR_CACHE, "run", REVISAR_TIMEOUT,
            *PREPARAR_VOLUMEN,
            "exec pip install -e .", "exec pip install pytest",
        ]
    assert docker.llamadas[-1] == ["docker", "rm", "-f", "-v", NOMBRE]


def test_cada_ejecucion_tiene_su_propio_nombre(repo: Path) -> None:
    uno = EntornoDocker(repo, CONFIG, "init", ejecutar=DockerFalso())
    otro = EntornoDocker(repo, CONFIG, "init", ejecutar=DockerFalso())
    assert uno.nombre != otro.nombre
    assert uno.nombre.startswith("harness-tareas-api-init-")
    assert len(uno.id_ejecucion) == 6


def test_al_empezar_borra_solo_los_restos_de_ejecuciones_terminadas(repo: Path) -> None:
    viva = {"harness": "1", "harness.pid": str(PID_VIVO), "harness.host": HOST}
    muerta = {"harness": "1", "harness.pid": "99999", "harness.host": HOST}
    de_otra_maquina = {"harness": "1", "harness.pid": "99999", "harness.host": "otra"}
    vieja = {"harness": "1", "harness.proyecto": "tareas-api"}  # sin pid: versión anterior
    inspeccion = _inspeccion(
        {"id": "id-viva", "nombre": "harness-tareas-api-init-aaaaaa", "etiquetas": viva},
        {"id": "id-muerta", "nombre": "harness-tareas-api-init-bbbbbb", "etiquetas": muerta},
        {"id": "id-otra", "nombre": "harness-tareas-api-t-cccccc", "etiquetas": de_otra_maquina},
        {"id": "id-vieja", "nombre": "harness-tareas-api-init", "etiquetas": vieja},
    )
    docker = DockerFalso({
        "ps": [(0, "id-viva\nid-muerta\nid-otra\nid-vieja\n")],
        "inspect": [(0, inspeccion)],
    })
    _iniciar(repo, docker, usar_cache=False)
    assert docker.llamadas[1] == [
        "docker", "ps", "-a", "-q", "--no-trunc", "--filter", "label=harness.proyecto=tareas-api",
    ]
    borrados = [a for a in docker.llamadas if a[1] == "rm"]
    assert borrados == [["docker", "rm", "-f", "-v", "id-muerta", "id-vieja"]]


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
        "docker", "exec", NOMBRE,
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
    assert docker.subcomandos()[11:] == [
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
CONTENEDOR = "harness-notas-api-notas-1-e1"
SERVICIO = "harness-notas-api-notas-1-e1-db"
ETIQUETAS_NOTAS = _etiquetas("notas-api", "NOTAS-1")


def _entorno_servicios(repo: Path, docker: DockerFalso, **kwargs) -> EntornoDocker:
    variables = resolver_variables(CONFIG_SERVICIOS, {"CLAVE_PG": "secreta"})
    return EntornoDocker(
        repo, CONFIG_SERVICIOS, "NOTAS-1", variables, ejecutar=docker, id_ejecucion=ID, **kwargs
    )


def _sin_dormir(segundos: float) -> None:
    pass


def test_servicios_en_red_privada_antes_que_el_proyecto(repo: Path) -> None:
    docker = DockerFalso()
    avisos: list[str] = []
    with _entorno_servicios(repo, docker) as entorno:
        entorno.iniciar(al_avanzar=avisos.append)
    assert docker.subcomandos() == [
        "version", "ps", "network", "image", "image", "network", "run",
        "exec pg_isready -h 127.0.0.1", *PREPARAR_CACHE, "run", REVISAR_TIMEOUT,
        *PREPARAR_VOLUMEN,
        "exec pip install -r requirements.txt", "rm", "network",
    ]
    # Los restos de antes se buscan por etiqueta, nunca por nombre.
    filtro = ["--filter", "label=harness.proyecto=notas-api"]
    assert docker.llamadas[1] == ["docker", "ps", "-a", "-q", "--no-trunc", *filtro]
    assert docker.llamadas[2] == ["docker", "network", "ls", "-q", "--no-trunc", *filtro]
    assert docker.llamadas[5] == ["docker", "network", "create", *ETIQUETAS_NOTAS, CONTENEDOR]
    assert docker.llamadas[-2] == ["docker", "rm", "-f", "-v", CONTENEDOR, SERVICIO]
    assert docker.llamadas[-1] == ["docker", "network", "rm", CONTENEDOR]
    assert "Esperando a que el servicio db esté listo..." in avisos


def test_borra_los_volumenes_anonimos_de_los_contenedores(repo: Path) -> None:
    # postgres declara un VOLUME: sin -v, cada ejecución dejaría un volumen huérfano.
    docker = DockerFalso()
    with _entorno_servicios(repo, docker) as entorno:
        entorno.iniciar()
    borrados = [a for a in docker.llamadas if a[1] == "rm"]
    assert borrados == [["docker", "rm", "-f", "-v", CONTENEDOR, SERVICIO]]


def test_argumentos_del_servicio_y_del_proyecto(repo: Path) -> None:
    docker = DockerFalso()
    _entorno_servicios(repo, docker).iniciar()
    servicio, proyecto = [a for a in docker.llamadas if a[1] == "run"]
    assert servicio == [
        "docker", "run", "-d", "--name", SERVICIO, *ETIQUETAS_NOTAS,
        "--network", CONTENEDOR, "--network-alias", "db",
        "-e", "POSTGRES_PASSWORD", "postgres:16",
    ]
    fin_etiquetas = 5 + len(ETIQUETAS_NOTAS)
    assert proyecto[:fin_etiquetas] == [
        "docker", "run", "-d", "--name", CONTENEDOR, *ETIQUETAS_NOTAS,
    ]
    assert proyecto[fin_etiquetas:fin_etiquetas + 4] == [
        "--network", CONTENEDOR, "-e", "DATABASE_URL",
    ]


def test_los_valores_de_las_variables_no_van_en_la_linea_de_comandos(repo: Path) -> None:
    docker = DockerFalso()
    _entorno_servicios(repo, docker).iniciar()
    assert not any("secreta" in argumento for a in docker.llamadas for argumento in a)
    indices = [i for i, a in enumerate(docker.llamadas) if a[1] == "run"]
    assert docker.variables[indices[0]] == {"POSTGRES_PASSWORD": "secreta"}
    proyecto = {"DATABASE_URL": "postgresql://postgres:secreta@db/notas"}
    assert docker.variables[indices[1]] == {**proyecto, **variables_cache(proyecto)[0]}


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
    for ruta in [".env", "backend/.env.local", ".env.example", ".venv/.env", ".envrc"]:
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
        f"type=bind,source={vacio},target=/app/.envrc,readonly",
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


def test_servicio_detenido_corta_sin_esperar_el_timeout(repo: Path) -> None:
    respuestas = {
        "pg_isready -h 127.0.0.1": [(2, "no response")] * 100,
        "inspect": [(0, "true\n"), (0, "false\n")],
        "logs": [(0, "Error: Database is uninitialized and superuser password is not specified.")],
    }
    docker = DockerFalso(respuestas)
    esperas: list[float] = []
    with pytest.raises(ErrorDocker) as error:
        with _entorno_servicios(repo, docker, dormir=esperas.append) as entorno:
            entorno.iniciar()
    mensaje = str(error.value)
    assert "El servicio 'db' se detuvo antes de estar listo" in mensaje
    assert "superuser password is not specified" in mensaje
    # Dos comprobaciones y una sola espera: no se esperaron los 30 s del timeout.
    assert docker.subcomandos().count("exec pg_isready -h 127.0.0.1") == 2
    assert esperas == [1.0]
    assert ["docker", "inspect", "-f", "{{.State.Running}}", SERVICIO] in docker.llamadas
    assert docker.llamadas[-1] == ["docker", "network", "rm", CONTENEDOR]


def test_si_docker_inspect_falla_se_sigue_esperando(repo: Path) -> None:
    respuestas = {
        "pg_isready -h 127.0.0.1": [(2, ""), (0, "")],
        "inspect": [(1, "Error response from daemon: timeout")],
    }
    docker = DockerFalso(respuestas)
    _entorno_servicios(repo, docker, dormir=_sin_dormir).iniciar()
    assert docker.subcomandos().count("exec pg_isready -h 127.0.0.1") == 2


# --- Caché de dependencias ----------------------------------------------------------------


def _run_proyecto(docker: DockerFalso) -> list[str]:
    return [a for a in docker.llamadas if a[1] == "run"][-1]


def _montajes_de(argumentos: list[str]) -> list[str]:
    return [argumentos[i + 1] for i, a in enumerate(argumentos) if a == "--mount"]


def test_la_cache_se_monta_fuera_del_repo_con_sus_variables(repo: Path) -> None:
    docker = DockerFalso()
    entorno = _iniciar(repo, docker)
    run = _run_proyecto(docker)
    assert "type=volume,source=harness-cache-tareas-api,target=/harness-cache" in _montajes_de(run)
    assert entorno.cache == EstadoCache("harness-cache-tareas-api", es_creado=False)
    indice = docker.llamadas.index(run)
    assert docker.variables[indice] == {
        "PIP_CACHE_DIR": "/harness-cache/pip",
        "UV_CACHE_DIR": "/harness-cache/uv",
        "UV_LINK_MODE": "copy",
        "npm_config_cache": "/harness-cache/npm",
        "GRADLE_USER_HOME": "/harness-cache/gradle",
        "MAVEN_OPTS": "-Dmaven.repo.local=/harness-cache/maven",
    }
    # Los valores van por el entorno del proceso, como las demás variables.
    assert "-e" in run and "PIP_CACHE_DIR" in run and "/harness-cache/pip" not in run


def test_la_cache_no_toca_los_montajes_de_solo_lectura(repo: Path) -> None:
    docker = DockerFalso()
    _iniciar(repo, docker)
    raiz = repo.resolve()
    montajes = _montajes_de(_run_proyecto(docker))
    assert montajes[0] == f"type=bind,source={raiz},target=/app"
    for ruta in (".git", "harness.yaml", ".harness"):
        assert f"type=bind,source={raiz / ruta},target=/app/{ruta},readonly" in montajes


def test_crea_el_volumen_si_no_existe_con_sus_etiquetas(repo: Path) -> None:
    docker = DockerFalso({"volume": [(1, "Error: no such volume"), (0, "")]})
    entorno = _iniciar(repo, docker)
    volumenes = [a for a in docker.llamadas if a[1] == "volume"]
    assert volumenes == [
        ["docker", "volume", "inspect", "harness-cache-tareas-api"],
        ["docker", "volume", "create", "--label", "harness=1",
         "--label", "harness.cache=tareas-api", "harness-cache-tareas-api"],
    ]
    assert entorno.cache.es_creado


def test_si_no_se_puede_crear_el_volumen_sigue_sin_cache(repo: Path) -> None:
    docker = DockerFalso({"volume": [(1, ""), (1, "no space left on device")]})
    entorno = _iniciar(repo, docker)
    assert not entorno.cache.es_usada
    assert "no space left on device" in entorno.cache.motivo
    assert not any("type=volume" in m for m in _montajes_de(_run_proyecto(docker)))
    assert "exec pip install -e ." in docker.subcomandos()


def test_cerrar_nunca_borra_el_volumen(repo: Path) -> None:
    docker = DockerFalso({"volume": [(1, ""), (0, "")]})
    _iniciar(repo, docker).cerrar()
    assert not any(a[1] == "volume" and a[2] == "rm" for a in docker.llamadas)
    assert not any("harness-cache-tareas-api" in a for a in docker.llamadas if a[1] == "rm")


@pytest.mark.parametrize(
    ("config", "kwargs", "motivo"),
    [
        (CONFIG, {"usar_cache": False}, "--sin-cache"),
        (
            CONFIG.model_copy(update={"docker": CONFIG.docker.model_copy(update={"cache": False})}),
            {},
            "docker.cache: false en harness.yaml",
        ),
    ],
)
def test_sin_cache_no_hay_volumen_ni_variables(
    repo: Path, config: ConfigProyecto, kwargs: dict, motivo: str
) -> None:
    docker = DockerFalso()
    with EntornoDocker(repo, config, "TAREAS-1", ejecutar=docker, **kwargs) as entorno:
        entorno.iniciar()
        assert entorno.cache == EstadoCache(motivo=motivo)
    assert docker.subcomandos()[:6] == [
        "version", "ps", "image", "run", REVISAR_TIMEOUT, "exec pip install -e .",
    ]
    assert docker.variables[docker.subcomandos().index("run")] == {}


def test_otra_ejecucion_del_proyecto_corre_sin_cache(repo: Path) -> None:
    otra = "harness-tareas-api-tareas-2-ffffff"
    viva = {"harness": "1", "harness.pid": str(PID_VIVO), "harness.host": HOST,
            "harness.ejecucion": "ffffff"}
    docker = DockerFalso({
        # El primer ps es el de los restos (ninguno); el segundo, el de la caché.
        "ps": [(0, ""), (0, "id-otra\n")],
        "inspect": [(0, _inspeccion({"id": "id-otra", "nombre": otra, "etiquetas": viva}))],
    })
    entorno = _iniciar(repo, docker)
    assert entorno.cache == EstadoCache(
        motivo=f"otra ejecución de tareas-api la está usando ({otra})"
    )
    ps = [a for a in docker.llamadas if a[1] == "ps"][1]
    assert ps[5:] == [
        "--filter", "label=harness.proyecto=tareas-api", "--filter", "status=running",
    ]
    assert "volume" not in docker.subcomandos()


def test_los_contenedores_de_la_misma_ejecucion_no_cuentan_como_otra(repo: Path) -> None:
    # El servicio de esta misma ejecución ya está corriendo cuando se decide la caché.
    propia = {"harness": "1", "harness.pid": str(PID_VIVO), "harness.host": HOST,
              "harness.ejecucion": ID}
    docker = DockerFalso({
        "ps": [(0, ""), (0, "id-db\n")],
        "inspect": [(0, _inspeccion({"id": "id-db", "nombre": f"{NOMBRE}-db",
                                     "etiquetas": propia}))],
    })
    assert _iniciar(repo, docker).cache.es_usada


def test_el_contenedor_lleva_las_etiquetas_de_la_ejecucion(repo: Path) -> None:
    docker = DockerFalso()
    _iniciar(repo, docker, usar_cache=False)
    run = _run_proyecto(docker)
    etiquetas = _etiquetas("tareas-api", "TAREAS-1")
    assert run[5:5 + len(etiquetas)] == etiquetas


def test_con_otro_usuario_le_da_la_cache_y_borra_la_configuracion_de_gradle(repo: Path) -> None:
    docker = DockerFalso({COMANDO_USUARIO: [(0, "1000:1000\n")]})
    _iniciar(repo, docker)
    preparar = next(a for a in docker.llamadas if a[:4] == ["docker", "exec", "-u", "0"])
    comando = preparar[-1]
    assert comando == comando_preparar("1000:1000")
    assert comando.startswith(
        "rm -rf /harness-cache/gradle/init.d /harness-cache/gradle/gradle.properties && "
    )
    assert "chown 1000:1000 /harness-cache /harness-cache/pip" in comando
    assert "chown -R" not in comando


@pytest.mark.parametrize("salida", ["", "root\n", "1000:1000; rm -rf /"])
def test_usuario_raro_detiene_con_un_error_claro(repo: Path, salida: str) -> None:
    docker = DockerFalso({COMANDO_USUARIO: [(0, salida)]})
    with pytest.raises(ErrorDocker, match="No se pudo preparar la caché de dependencias"):
        with EntornoDocker(repo, CONFIG, "T", ejecutar=docker) as entorno:
            entorno.iniciar()
    # Lo que sale del contenedor nunca llega a un comando de root.
    assert not any(a[:4] == ["docker", "exec", "-u", "0"] for a in docker.llamadas)


def test_respeta_las_variables_de_cache_del_proyecto(repo: Path) -> None:
    config = CONFIG.model_copy(update={"variables_entorno": {
        "PIP_CACHE_DIR": "/otra/pip", "MAVEN_OPTS": "-Xmx1g",
    }})
    docker = DockerFalso()
    with EntornoDocker(repo, config, "TAREAS-1", ejecutar=docker) as entorno:
        entorno.iniciar()
        assert entorno.cache.variables_respetadas == ("PIP_CACHE_DIR",)
    variables = docker.variables[docker.subcomandos().index("run")]
    assert variables["PIP_CACHE_DIR"] == "/otra/pip"
    assert variables["MAVEN_OPTS"] == "-Xmx1g -Dmaven.repo.local=/harness-cache/maven"


def test_maven_opts_con_su_propio_repositorio_se_respeta() -> None:
    variables, respetadas = variables_cache({"MAVEN_OPTS": "-Dmaven.repo.local=/m2"})
    assert "MAVEN_OPTS" not in variables and respetadas == ["MAVEN_OPTS"]


def test_avisa_la_cache_y_mide_la_instalacion(repo: Path) -> None:
    # Cada comando lee el reloj al empezar y al terminar: instalar (3 s + 7 s), pytest,
    # reinstalar (8 s + 2 s) y pytest.
    tiempos = [0.0, 3.0, 3.0, 10.0, 20.0, 21.0, 30.0, 38.0, 38.0, 40.0, 41.0, 42.0]
    caches: list[EstadoCache] = []
    instalaciones: list[Instalacion] = []
    entorno = _iniciar(
        repo, DockerFalso(), reloj=lambda: tiempos.pop(0),
        al_cache=caches.append, al_instalar=instalaciones.append,
    )
    assert caches == [entorno.cache] and entorno.cache.es_usada
    assert instalaciones == [Instalacion(10.0, es_ok=True)]
    entorno.ejecutar("pytest")
    entorno.marcar_cambio("pyproject.toml")
    entorno.ejecutar("pytest")
    assert instalaciones[1] == Instalacion(10.0, es_ok=True, archivo_cambiado="pyproject.toml")
    assert instalaciones[1].es_reinstalacion


def test_una_instalacion_fallida_tambien_se_mide(repo: Path) -> None:
    instalaciones: list[Instalacion] = []
    docker = DockerFalso({"pip install pytest": [(1, "sin red")]})
    with pytest.raises(ErrorDocker, match="sin red"):
        _iniciar(repo, docker, al_instalar=instalaciones.append)
    assert [i.es_ok for i in instalaciones] == [False]


def test_sin_comandos_de_instalacion_no_hay_nada_que_medir(repo: Path) -> None:
    config = CONFIG.model_copy(update={"docker": CONFIG.docker.model_copy(update={"instalar": []})})
    instalaciones: list[Instalacion] = []
    with EntornoDocker(
        repo, config, "T", ejecutar=DockerFalso(), al_instalar=instalaciones.append
    ) as entorno:
        entorno.iniciar()
    assert instalaciones == []


def test_conservar_no_borra_nada_y_dice_que_quedo(repo: Path) -> None:
    (repo / ".env").write_text("CLAVE=real\n", encoding="utf-8")
    docker = DockerFalso()
    with _entorno_servicios(repo, docker, conservar=True) as entorno:
        entorno.iniciar()
    assert "rm" not in docker.subcomandos()
    assert ["docker", "network", "rm", CONTENEDOR] not in docker.llamadas
    assert entorno.conservados == Conservados(CONTENEDOR, ((SERVICIO, "db"),), CONTENEDOR)
    assert entorno.conservados.contenedores == [SERVICIO, CONTENEDOR]
    # Todo lo conservado lleva la etiqueta, para que harness limpiar lo encuentre después.
    for argumentos in [a for a in docker.llamadas if a[1] == "run" or a[2:3] == ["create"]]:
        assert "harness.conservar=1" in argumentos
    # El archivo vacío que tapa el .env sigue montado: no se borra.
    tapa = next(m for m in _montajes_de(_run_proyecto(docker)) if m.endswith("/app/.env,readonly"))
    assert Path(tapa.split(",")[1].removeprefix("source=")).exists()


def test_conservar_aunque_falle_la_instalacion(repo: Path) -> None:
    # Es justo cuando más sirve entrar a mirar el contenedor.
    docker = DockerFalso({"pip install -e .": [(1, "error")]})
    entorno = EntornoDocker(
        repo, CONFIG, "TAREAS-1", ejecutar=docker, id_ejecucion=ID, conservar=True
    )
    with pytest.raises(ErrorDocker):
        with entorno:
            entorno.iniciar()
    assert "rm" not in docker.subcomandos()
    assert entorno.conservados == Conservados(NOMBRE)


def test_conservar_sin_nada_creado(repo: Path) -> None:
    docker = DockerFalso({"version": [(1, "")]})
    entorno = EntornoDocker(repo, CONFIG, "T", ejecutar=docker, conservar=True)
    with pytest.raises(ErrorDocker):
        with entorno:
            entorno.iniciar()
    assert entorno.conservados is not None and not entorno.conservados.hay_algo


def test_sin_conservar_no_hay_conservados(repo: Path) -> None:
    with _iniciar(repo, DockerFalso()) as entorno:
        pass
    assert entorno.conservados is None


def test_los_restos_conservados_no_se_borran_al_empezar(repo: Path) -> None:
    conservado = {"harness": "1", "harness.pid": "99999", "harness.host": HOST,
                  "harness.conservar": "1"}
    docker = DockerFalso({
        "ps": [(0, "id-conservado\n")],
        "inspect": [(0, _inspeccion({"id": "id-conservado", "nombre": "harness-x",
                                     "etiquetas": conservado}))],
    })
    _iniciar(repo, docker, usar_cache=False).cerrar()
    assert [a for a in docker.llamadas if a[1] == "rm"] == [["docker", "rm", "-f", "-v", NOMBRE]]


SALIDA_PULL = [
    "16: Pulling from library/postgres\n",
    "a2abf6c4d29d: Pulling fs layer\n",
    "b3bbf6c4d29e: Pulling fs layer\n",
    "c4cbf6c4d29f: Already exists\n",
    "a2abf6c4d29d: Pull complete\n",
    None,
    "b3bbf6c4d29e: Pull complete\n",
]


def test_la_descarga_muestra_las_capas_y_usa_el_timeout_de_harness_yaml(repo: Path) -> None:
    pedidos: list[tuple[list[str], float]] = []

    def descargar_falso(argumentos, timeout, al_avanzar):
        pedidos.append((argumentos, timeout))
        for linea in SALIDA_PULL:
            al_avanzar(linea)
        return 0, "".join(linea for linea in SALIDA_PULL if linea)

    config = CONFIG_SERVICIOS.model_copy(update={
        "docker": CONFIG_SERVICIOS.docker.model_copy(update={"timeout_descarga_segundos": 1800})
    })
    docker = DockerFalso({"image": [(1, "No such image"), (0, "[]")]})
    reloj = iter([0.0, 0.0, 1.0, 2.0, 3.0, 65.0, 66.0, 70.0, *[71.0] * 50])
    avisos: list[str] = []
    variables = resolver_variables(config, {"CLAVE_PG": "secreta"})
    EntornoDocker(
        repo, config, "NOTAS-1", variables, ejecutar=docker, descargar=descargar_falso,
        reloj=lambda: next(reloj),
    ).iniciar(al_avanzar=avisos.append)
    assert pedidos == [(["docker", "pull", "postgres:16"], 1800)]
    descarga = [a for a in avisos if a.startswith("Descargando")]
    assert descarga[0] == "Descargando la imagen postgres:16 (solo la primera vez)..."
    # "Already exists" ya cuenta como lista; el None (sin líneas) solo avanza el tiempo.
    assert "Descargando la imagen postgres:16: 1 de 3 capas (3 s)..." in descarga
    assert "Descargando la imagen postgres:16: 2 de 3 capas (1 min 05 s)..." in descarga
    assert "Descargando la imagen postgres:16: 2 de 3 capas (1 min 06 s)..." in descarga
    assert descarga[-1] == "Descargando la imagen postgres:16: 3 de 3 capas (1 min 10 s)..."


def test_la_descarga_que_tarda_demasiado_dice_que_clave_subir(repo: Path) -> None:
    docker = DockerFalso({"image": [(1, "No such image")]})
    entorno = _entorno_servicios(
        repo, docker, descargar=lambda *_: (CODIGO_TIEMPO_AGOTADO, "")
    )
    with pytest.raises(ErrorDocker) as error:
        entorno.iniciar()
    mensaje = str(error.value)
    assert "tardó más de 600 s" in mensaje
    assert "docker.timeout_descarga_segundos" in mensaje


def test_ejecutar_proceso_con_lineas_pasa_cada_linea() -> None:
    lineas: list[str | None] = []
    codigo, salida = ejecutar_proceso_con_lineas(
        [sys.executable, "-c", "print('uno'); print('dos')"], 30, lineas.append
    )
    assert codigo == 0
    assert salida.splitlines() == ["uno", "dos"]
    assert [linea.strip() for linea in lineas if linea] == ["uno", "dos"]


def test_ejecutar_proceso_con_lineas_corta_por_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entorno_docker, "INTERVALO_PROGRESO_SEGUNDOS", 0.1)
    ticks: list[str | None] = []
    codigo, _ = ejecutar_proceso_con_lineas(
        [sys.executable, "-c", "import time; time.sleep(30)"], 0.5, ticks.append
    )
    assert codigo == CODIGO_TIEMPO_AGOTADO
    # Sin líneas, igual se avisa que el tiempo corre.
    assert ticks and all(t is None for t in ticks)


def test_imagen_sin_timeout_corre_sin_el_y_reinicia_si_se_pasa(repo: Path) -> None:
    avisos: list[str] = []
    docker = DockerFalso({
        COMANDO_TIMEOUT: [(1, "")],
        "pytest -q": [(CODIGO_TIEMPO_AGOTADO, "")],
    })
    entorno = _iniciar(repo, docker, al_aviso=avisos.append)
    assert avisos == [
        "La imagen 'python:3.12-slim' no trae el comando 'timeout': si un comando se pasa del "
        "tiempo, se reinicia el contenedor para cortarlo."
    ]
    instalar = next(a for a in docker.llamadas if a[-1] == "pip install -e .")
    assert instalar == ["docker", "exec", NOMBRE, "sh", "-c", "pip install -e ."]
    resultado = entorno.ejecutar("pytest -q")
    assert resultado.es_tiempo_agotado
    assert docker.llamadas[-1] == ["docker", "restart", "-t", "0", NOMBRE]


def test_imagen_sin_timeout_que_termina_a_tiempo_no_reinicia(repo: Path) -> None:
    docker = DockerFalso({COMANDO_TIMEOUT: [(1, "")]})
    entorno = _iniciar(repo, docker)
    assert entorno.ejecutar("pytest -q").codigo == 0
    assert "restart" not in docker.subcomandos()


def test_imagen_sin_sh_da_un_error_claro(repo: Path) -> None:
    salida = (
        'OCI runtime exec failed: exec failed: unable to start container process: exec: "sh": '
        "executable file not found in $PATH: unknown"
    )
    docker = DockerFalso({COMANDO_TIMEOUT: [(126, salida)]})
    with pytest.raises(ErrorDocker, match="no tiene 'sh'"):
        with EntornoDocker(repo, CONFIG, "T", ejecutar=docker) as entorno:
            entorno.iniciar()
    assert docker.subcomandos()[-1] == "rm"


CONFIG_USUARIO_HOST = CONFIG.model_copy(
    update={"docker": CONFIG.docker.model_copy(update={"usuario_host": True})}
)


@pytest.fixture
def en_linux_como_1000(monkeypatch: pytest.MonkeyPatch) -> None:
    """Como si el harness corriera en Linux con el usuario 1000:1000."""
    monkeypatch.setattr(entorno_docker, "usuario_del_host", lambda: "1000:1000")


def _con_usuario_host(repo: Path, docker: DockerFalso, config: ConfigProyecto) -> list[str]:
    EntornoDocker(repo, config, "TAREAS-1", ejecutar=docker, id_ejecucion=ID).iniciar()
    return _run_proyecto(docker)


@pytest.mark.usefixtures("en_linux_como_1000")
def test_usuario_host_corre_con_el_usuario_y_un_home(repo: Path) -> None:
    # Dentro del contenedor, 'id' devuelve el usuario del host: la caché se le da a él.
    docker = DockerFalso({COMANDO_USUARIO: [(0, "1000:1000\n")]})
    run = _con_usuario_host(repo, docker, CONFIG_USUARIO_HOST)
    assert run[run.index("--user") + 1] == "1000:1000"
    assert run[run.index("HOME") - 1] == "-e"
    assert docker.variables[docker.llamadas.index(run)]["HOME"] == "/tmp/harness-home"
    preparar = next(a for a in docker.llamadas if a[:4] == ["docker", "exec", "-u", "0"])
    assert "chown 1000:1000" in preparar[-1]


@pytest.mark.usefixtures("en_linux_como_1000")
def test_usuario_host_no_toca_los_servicios(repo: Path) -> None:
    config = CONFIG_SERVICIOS.model_copy(update={
        "docker": CONFIG_SERVICIOS.docker.model_copy(update={"usuario_host": True})
    })
    variables = resolver_variables(config, {"CLAVE_PG": "secreta"})
    docker = DockerFalso()
    EntornoDocker(repo, config, "NOTAS-1", variables, ejecutar=docker, id_ejecucion=ID).iniciar()
    servicio, proyecto = [a for a in docker.llamadas if a[1] == "run"]
    assert "--user" not in servicio and "--user" in proyecto


@pytest.mark.usefixtures("en_linux_como_1000")
def test_usuario_host_respeta_el_home_del_proyecto(repo: Path) -> None:
    config = CONFIG_USUARIO_HOST.model_copy(update={"variables_entorno": {"HOME": "/home/app"}})
    docker = DockerFalso()
    run = _con_usuario_host(repo, docker, config)
    assert docker.variables[docker.llamadas.index(run)]["HOME"] == "/home/app"


@pytest.mark.usefixtures("en_linux_como_1000")
def test_sin_usuario_host_no_hay_user(repo: Path) -> None:
    run = _con_usuario_host(repo, DockerFalso(), CONFIG)
    assert "--user" not in run and "HOME" not in run


def test_fuera_de_linux_no_hay_user(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entorno_docker, "usuario_del_host", lambda: None)
    run = _con_usuario_host(repo, DockerFalso(), CONFIG_USUARIO_HOST)
    assert "--user" not in run and "HOME" not in run


@pytest.mark.parametrize(("plataforma", "esperado"), [("linux", "1000:1001"), ("win32", None)])
def test_usuario_del_host(
    monkeypatch: pytest.MonkeyPatch, plataforma: str, esperado: str | None
) -> None:
    monkeypatch.setattr(entorno_docker.sys, "platform", plataforma)
    monkeypatch.setattr(entorno_docker.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(entorno_docker.os, "getgid", lambda: 1001, raising=False)
    assert entorno_docker.usuario_del_host() == esperado


def test_verificar_docker_mide_lo_que_tarda() -> None:
    tiempos = [10.0, 12.5]
    assert verificar_docker(DockerFalso(), reloj=lambda: tiempos.pop(0)) == 2.5


@pytest.mark.parametrize(
    ("respuesta", "mensaje"),
    [
        ((1, "Cannot connect to the Docker daemon"), MENSAJE_DOCKER_NO_CORRE),
        ((CODIGO_TIEMPO_AGOTADO, ""), "Docker no respondió en 60 s"),
    ],
)
def test_verificar_docker_con_docker_cerrado(respuesta: tuple[int, str], mensaje: str) -> None:
    with pytest.raises(ErrorDocker, match=mensaje):
        verificar_docker(DockerFalso({"version": [respuesta]}))
