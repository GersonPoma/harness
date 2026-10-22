"""Pruebas contra Docker de verdad. Se saltan si Docker no está corriendo.

Solo estas:  uv run pytest -m docker
"""

import subprocess
from pathlib import Path

import pytest

from harness.config_proyecto import ConfigComandos, ConfigDocker, ConfigProyecto
from harness.entorno_docker import EntornoDocker
from harness.variables import resolver_variables


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


def _config(timeout: int = 60) -> ConfigProyecto:
    return ConfigProyecto.model_validate({
        "version": 1,
        "proyecto": "prueba",
        "docker": {"imagen": IMAGEN},
        "comandos": {"tests": "python -c 'print(1)'"},
        "limites": {"timeout_comando_segundos": timeout},
    })


def _nombres_contenedores() -> str:
    proceso = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True
    )
    return proceso.stdout


def test_ejecuta_en_el_contenedor_y_ve_el_repo(repo: Path) -> None:
    with EntornoDocker(repo, _config(), "prueba-real") as entorno:
        entorno.iniciar()
        resultado = entorno.ejecutar("python src/app.py && cat harness.yaml")
        assert resultado.codigo == 0
        assert "hola" in resultado.salida and "version: 1" in resultado.salida
        # Lo que escribe el contenedor aparece en el disco del host.
        assert entorno.ejecutar("python -c \"open('nuevo.txt','w').write('x')\"").codigo == 0
        assert (repo / "nuevo.txt").read_text() == "x"
    assert "harness-prueba-prueba-real" not in _nombres_contenedores()


def test_git_y_harness_yaml_son_de_solo_lectura(repo: Path) -> None:
    with EntornoDocker(repo, _config(), "prueba-ro") as entorno:
        entorno.iniciar()
        for ruta in [".git/hooks-pre-commit", "harness.yaml", ".harness/x.jsonl"]:
            resultado = entorno.ejecutar(f"python -c \"open('{ruta}','w').write('x')\"")
            assert resultado.codigo != 0, ruta
            assert "Read-only file system" in resultado.salida
    assert (repo / "harness.yaml").read_text() == "version: 1\n"
    assert not (repo / ".git" / "hooks-pre-commit").exists()


def test_timeout_corta_el_proceso_dentro_del_contenedor(repo: Path) -> None:
    with EntornoDocker(repo, _config(timeout=2), "prueba-timeout") as entorno:
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
    proceso = subprocess.run(
        ["docker", "volume", "ls", "-q"], capture_output=True, text=True
    )
    return set(proceso.stdout.split())


def _redes() -> str:
    proceso = subprocess.run(
        ["docker", "network", "ls", "--format", "{{.Name}}"], capture_output=True, text=True
    )
    return proceso.stdout


def test_servicio_en_red_privada(repo: Path) -> None:
    config = ConfigProyecto.model_validate({
        "version": 1,
        "proyecto": "prueba",
        "docker": {"imagen": IMAGEN},
        "comandos": {"tests": "true"},
        "servicios": [{
            "nombre": "cache",
            "imagen": "redis:7-alpine",
            "listo_cuando": "redis-cli ping",
        }],
        "variables_entorno": {"CACHE_HOST": "cache", "CLAVE": "${CLAVE_PRUEBA_HARNESS}"},
    })
    variables = resolver_variables(config, {"CLAVE_PRUEBA_HARNESS": "valor-secreto"})
    volumenes_antes = _volumenes()
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
    assert "harness-prueba-prueba-servicio" not in _nombres_contenedores()
    assert "harness-prueba-prueba-servicio" not in _redes()
    # redis declara VOLUME /data: su volumen anónimo se borra junto con el contenedor.
    assert _volumenes() - volumenes_antes == set()
