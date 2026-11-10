"""Pruebas contra Docker de verdad. Se saltan si Docker no está corriendo.

Solo estas:  uv run pytest -m docker

Cuidados con el Docker de quien las corre: cada test usa un proyecto con nombre único
(prueba-<uuid>) y borra solo lo que creó (por id o por ese nombre único). harness limpiar se
llama siempre con un filtro de etiqueta propio del test, así nunca ve nada del usuario. Una
imagen que el test tuvo que descargar se borra al final (por id); una que ya estaba, no se toca.
"""

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from harness.config_proyecto import ConfigProyecto
from harness.entorno_docker import (
    EntornoDocker,
    ProgresoDescarga,
    ejecutar_proceso,
    ejecutar_proceso_con_lineas,
)
from harness.etiquetas import host_actual
from harness.init import verificar_config
from harness.limpieza import borrar, planear
from harness.variables import VariablesResueltas, resolver_variables


def _docker_corriendo() -> bool:
    try:
        proceso = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proceso.returncode == 0


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _docker_corriendo(), reason="Docker no está corriendo"),
]

IMAGEN = "python:3.12-slim"
IMAGEN_REDIS = "redis:7-alpine"
IMAGEN_CHICA = "hello-world"


def _docker(*argumentos: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *argumentos], capture_output=True, text=True)


def _proyecto_unico() -> str:
    return f"prueba-{uuid.uuid4().hex[:8]}"


def _config(proyecto: str, timeout: int = 60, **extra: object) -> ConfigProyecto:
    return ConfigProyecto.model_validate({
        "version": 1,
        "proyecto": proyecto,
        "docker": {"imagen": IMAGEN, "cache": False},
        "comandos": {"tests": "python -c 'print(1)'"},
        "limites": {"timeout_comando_segundos": timeout},
        **extra,
    })


def _ids(tipo: str, etiqueta: str) -> list[str]:
    """Contenedores ('ps') o redes ('network') con la etiqueta (todos los estados)."""
    if tipo == "ps":
        salida = _docker("ps", "-a", "-q", "--filter", f"label={etiqueta}").stdout
    else:
        salida = _docker("network", "ls", "-q", "--filter", f"label={etiqueta}").stdout
    return salida.split()


def _nada_queda(proyecto: str) -> bool:
    etiqueta = f"harness.proyecto={proyecto}"
    return _ids("ps", etiqueta) == [] and _ids("network", etiqueta) == []


@pytest.fixture
def proyecto() -> Iterator[str]:
    """Un proyecto único; al terminar se borra todo lo que tenga su etiqueta (solo eso)."""
    nombre = _proyecto_unico()
    yield nombre
    etiqueta = f"harness.proyecto={nombre}"
    for contenedor in _ids("ps", etiqueta):
        _docker("rm", "-f", "-v", contenedor)
    for red in _ids("network", etiqueta):
        _docker("network", "rm", red)


def _id_imagen(imagen: str) -> str | None:
    resultado = _docker("image", "inspect", "--format", "{{.Id}}", imagen)
    return resultado.stdout.strip() if resultado.returncode == 0 else None


@contextmanager
def _imagen_temporal(imagen: str) -> Iterator[bool]:
    """Si la imagen no estaba y el test la descarga, se borra al salir (por id, solo esa).

    Devuelve si ya estaba antes: en ese caso nunca se toca.
    """
    ya_estaba = _id_imagen(imagen) is not None
    try:
        yield ya_estaba
    finally:
        descargada = None if ya_estaba else _id_imagen(imagen)
        if descargada:
            _docker("rmi", descargada)


def test_ejecuta_en_el_contenedor_y_ve_el_repo(repo: Path, proyecto: str) -> None:
    with EntornoDocker(repo, _config(proyecto), "prueba-real") as entorno:
        entorno.iniciar()
        resultado = entorno.ejecutar("python src/app.py && cat harness.yaml")
        assert resultado.codigo == 0
        assert "hola" in resultado.salida and "version: 1" in resultado.salida
        # Lo que escribe el contenedor aparece en el disco del host.
        assert entorno.ejecutar("python -c \"open('nuevo.txt','w').write('x')\"").codigo == 0
        assert (repo / "nuevo.txt").read_text() == "x"
    assert _nada_queda(proyecto)


def test_env_se_ve_vacio_dentro_del_contenedor(repo: Path, proyecto: str) -> None:
    (repo / ".env").write_text("API_KEY=sk-real\n", encoding="utf-8")
    (repo / "backend").mkdir()
    (repo / "backend" / ".env.local").write_text("CLAVE=real\n", encoding="utf-8")
    (repo / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
    with EntornoDocker(repo, _config(proyecto), "prueba-env") as entorno:
        entorno.iniciar()
        for ruta in [".env", "backend/.env.local"]:
            resultado = entorno.ejecutar(f"cat {ruta}")
            assert resultado.codigo == 0, resultado.salida
            assert resultado.salida == "", ruta
        # La plantilla sin secretos sí se ve.
        assert entorno.ejecutar("cat .env.example").salida == "API_KEY=\n"
        sobrescribir = entorno.ejecutar("python -c \"open('.env','w').write('x')\"")
        assert sobrescribir.codigo != 0
        assert "Read-only file system" in sobrescribir.salida
    assert (repo / ".env").read_text(encoding="utf-8") == "API_KEY=sk-real\n"
    assert (repo / "backend" / ".env.local").read_text(encoding="utf-8") == "CLAVE=real\n"


def test_git_y_harness_yaml_son_de_solo_lectura(repo: Path, proyecto: str) -> None:
    with EntornoDocker(repo, _config(proyecto), "prueba-ro") as entorno:
        entorno.iniciar()
        for ruta in [".git/hooks-pre-commit", "harness.yaml", ".harness/x.jsonl"]:
            resultado = entorno.ejecutar(f"python -c \"open('{ruta}','w').write('x')\"")
            assert resultado.codigo != 0, ruta
            assert "Read-only file system" in resultado.salida
    assert (repo / "harness.yaml").read_text() == "version: 1\n"
    assert not (repo / ".git" / "hooks-pre-commit").exists()


def test_timeout_corta_el_proceso_dentro_del_contenedor(repo: Path, proyecto: str) -> None:
    with EntornoDocker(repo, _config(proyecto, timeout=2), "prueba-timeout") as entorno:
        entorno.iniciar()
        resultado = entorno.ejecutar("python -c 'import time; time.sleep(30)'")
        assert resultado.es_tiempo_agotado
        # Lista la línea de comandos de cada proceso vivo del contenedor.
        procesos = entorno.ejecutar(
            "python -c \"import os; print([open(f'/proc/{p}/cmdline', 'rb').read() "
            "for p in os.listdir('/proc') if p.isdigit()])\""
        )
        assert "sleep(30)" not in procesos.salida


def _volumenes() -> set[str]:
    return set(_docker("volume", "ls", "-q").stdout.split())


def test_servicio_en_red_privada(repo: Path, proyecto: str) -> None:
    config = _config(
        proyecto,
        servicios=[{
            "nombre": "cache",
            "imagen": IMAGEN_REDIS,
            "listo_cuando": "redis-cli ping",
        }],
        variables_entorno={"CACHE_HOST": "cache", "CLAVE": "${CLAVE_PRUEBA_HARNESS}"},
    )
    variables = resolver_variables(config, {"CLAVE_PRUEBA_HARNESS": "valor-secreto"})
    volumenes_antes = _volumenes()
    with _imagen_temporal(IMAGEN_REDIS):
        with EntornoDocker(repo, config, "prueba-servicio", variables) as entorno:
            entorno.iniciar()
            # El proyecto llega al servicio por su alias y habla el protocolo de Redis.
            resultado = entorno.ejecutar(
                "python -c \"import os, socket; s = socket.create_connection("
                "(os.environ['CACHE_HOST'], 6379)); s.sendall(b'PING\\r\\n'); "
                "print(s.recv(16), os.environ['CLAVE'])\""
            )
            assert resultado.codigo == 0, resultado.salida
            assert "+PONG" in resultado.salida and "valor-secreto" in resultado.salida
        assert _nada_queda(proyecto)
    # redis declara VOLUME /data: su volumen anónimo se borra junto con el contenedor.
    assert _volumenes() - volumenes_antes == set()


def test_verificar_config_de_init(repo: Path, proyecto: str) -> None:
    config = ConfigProyecto.model_validate({
        "version": 1,
        "proyecto": proyecto,
        "docker": {
            "imagen": IMAGEN, "cache": False,
            "instalar": "python -c 'open(\"instalado\", \"w\")'",
        },
        "comandos": {"tests": "python -c 'import os; assert os.path.exists(\"instalado\")'"},
    })
    resultado = verificar_config(repo, config, VariablesResueltas())
    assert resultado.codigo == 0, resultado.salida
    assert _nada_queda(proyecto)


def test_la_cache_sobrevive_entre_ejecuciones(repo: Path, proyecto: str) -> None:
    # Un proyecto con nombre único: el test crea su propio volumen y borra solo ese.
    volumen = f"harness-cache-{proyecto}"
    config = ConfigProyecto.model_validate({
        "version": 1,
        "proyecto": proyecto,
        "docker": {"imagen": IMAGEN},
        "comandos": {"tests": "true"},
    })
    try:
        with EntornoDocker(repo, config, "uno") as entorno:
            entorno.iniciar()
            assert entorno.cache.es_creado
            escribir = entorno.ejecutar(
                'echo hola > "$PIP_CACHE_DIR/marca" && mkdir -p "$GRADLE_USER_HOME/init.d" '
                '&& touch "$GRADLE_USER_HOME/init.d/x.gradle"'
            )
            assert escribir.codigo == 0, escribir.salida
        inspeccion = _docker("volume", "inspect", "-f", "{{json .Labels}}", volumen)
        assert inspeccion.returncode == 0, "cerrar() no debe borrar el volumen"
        assert f'"harness.cache":"{proyecto}"' in inspeccion.stdout
        with EntornoDocker(repo, config, "dos") as entorno:
            entorno.iniciar()
            assert entorno.cache.es_usada and not entorno.cache.es_creado
            leer = entorno.ejecutar('cat "$PIP_CACHE_DIR/marca"; ls "$GRADLE_USER_HOME"')
            assert leer.salida.startswith("hola"), leer.salida
            # La configuración que Gradle ejecutaría en cada build no sobrevive.
            assert "init.d" not in leer.salida
        # Nada de la caché aparece en el repo.
        assert not (repo / "marca").exists() and not list(repo.rglob("marca"))
    finally:
        _docker("volume", "rm", "-f", volumen)


# --- Fase 11 ------------------------------------------------------------------------------


def test_dos_ejecuciones_vivas_del_mismo_proyecto_no_se_borran(repo: Path, proyecto: str) -> None:
    # Antes, las dos se llamaban harness-<proyecto>-init y la segunda borraba a la primera.
    config = _config(proyecto)
    with EntornoDocker(repo, config, "init") as primera:
        primera.iniciar()
        with EntornoDocker(repo, config, "init") as segunda:
            segunda.iniciar()
            assert primera.nombre != segunda.nombre
            assert primera.ejecutar("python -c 'print(1)'").codigo == 0
            assert segunda.ejecutar("python -c 'print(2)'").codigo == 0
        assert primera.ejecutar("python -c 'print(3)'").codigo == 0
    assert _nada_queda(proyecto)


def test_conservar_deja_el_contenedor_y_limpiar_no_lo_toca_mientras_vive(
    repo: Path, proyecto: str
) -> None:
    with EntornoDocker(repo, _config(proyecto), "conservar", conservar=True) as entorno:
        entorno.iniciar()
    assert entorno.conservados and entorno.conservados.proyecto == entorno.nombre
    etiquetas = json.loads(
        _docker("inspect", "-f", "{{json .Config.Labels}}", entorno.nombre).stdout
    )
    assert etiquetas["harness.conservar"] == "1"
    # Es de este proceso (que sigue vivo): limpiar lo muestra como "no se toca".
    plan = planear(ejecutar_proceso, filtro_extra=[f"label=harness.proyecto={proyecto}"])
    assert plan.a_borrar == []
    assert [e.nombre for e, _ in plan.omitidos] == [entorno.nombre]
    # El fixture `proyecto` lo borra al final (solo este, por la etiqueta del proyecto).


def _pid_terminado() -> int:
    proceso = subprocess.Popen([sys.executable, "-c", "pass"])
    proceso.wait()
    return proceso.pid


def _contenedor_con_dueno(proyecto: str, nombre: str, pid: int) -> str:
    resultado = _docker(
        "run", "-d", "--name", nombre, "--label", "harness=1", "--label", f"harness.pid={pid}",
        "--label", f"harness.host={host_actual()}", "--label", f"harness.proyecto={proyecto}",
        IMAGEN, "sleep", "infinity",
    )
    assert resultado.returncode == 0, resultado.stderr
    return resultado.stdout.strip()


def test_limpiar_borra_el_huerfano_y_deja_el_vivo(proyecto: str) -> None:
    huerfano = _contenedor_con_dueno(proyecto, f"harness-{proyecto}-huerfano", _pid_terminado())
    vivo = _contenedor_con_dueno(proyecto, f"harness-{proyecto}-vivo", os.getpid())
    plan = planear(ejecutar_proceso, filtro_extra=[f"label=harness.proyecto={proyecto}"])
    assert [e.id for e in plan.a_borrar] == [huerfano]
    assert [e.id for e, _ in plan.omitidos] == [vivo]
    resultado = borrar(ejecutar_proceso, plan)
    assert resultado.fallidos == []
    assert _ids("ps", f"harness.proyecto={proyecto}") == [vivo[:12]]


def test_limpiar_cache_borra_solo_el_volumen_del_test(proyecto: str) -> None:
    volumen = f"harness-cache-{proyecto}"
    creado = _docker(
        "volume", "create", "--label", "harness=1", "--label", f"harness.cache={proyecto}",
        volumen,
    )
    assert creado.returncode == 0, creado.stderr
    try:
        plan = planear(
            ejecutar_proceso, con_cache=True, proyecto_cache=proyecto,
            filtro_extra=[f"label=harness.cache={proyecto}"],
        )
        assert [(e.tipo, e.nombre) for e in plan.a_borrar] == [("volumen", volumen)]
        assert borrar(ejecutar_proceso, plan).fallidos == []
        assert volumen not in _volumenes()
    finally:
        _docker("volume", "rm", "-f", volumen)


def test_progreso_de_la_descarga_con_una_imagen_chica() -> None:
    with _imagen_temporal(IMAGEN_CHICA) as ya_estaba:
        if ya_estaba:
            pytest.skip(f"{IMAGEN_CHICA} ya estaba descargada: el test no la toca")
        progreso = ProgresoDescarga(IMAGEN_CHICA, 0.0)
        lineas: list[str] = []

        def al_avanzar(linea: str | None) -> None:
            if linea:
                lineas.append(linea)
                progreso.anotar(linea)

        codigo, salida = ejecutar_proceso_con_lineas(
            ["docker", "pull", IMAGEN_CHICA], 300, al_avanzar
        )
        assert codigo == 0, salida
        assert lineas, "docker pull no escribió nada"
        assert progreso.mensaje(1.0).endswith("capas (1 s)..."), (progreso.mensaje(1.0), lineas)
    assert _id_imagen(IMAGEN_CHICA) is None
