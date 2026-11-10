import json
import os
import subprocess
import sys

import pytest

from harness.etiquetas import (
    CONTENEDOR,
    RED,
    VOLUMEN,
    argumentos_etiquetas,
    es_de_ejecucion_viva,
    etiquetas_ejecucion,
    listar,
    proceso_vivo,
)


def test_el_propio_proceso_esta_vivo() -> None:
    assert proceso_vivo(os.getpid())


def test_un_proceso_que_termino_no_esta_vivo() -> None:
    proceso = subprocess.Popen([sys.executable, "-c", "pass"])
    proceso.wait()
    assert not proceso_vivo(proceso.pid)


def test_pid_invalido_no_esta_vivo() -> None:
    assert not proceso_vivo(0)


@pytest.mark.parametrize(
    ("etiquetas", "es_viva"),
    [
        ({"harness.pid": "10", "harness.host": "pc"}, True),
        ({"harness.pid": "11", "harness.host": "pc"}, False),
        # De otra máquina (Docker compartido): nunca se toca desde aquí.
        ({"harness.pid": "11", "harness.host": "otra"}, True),
        # Sin pid: de una versión anterior del harness, que no lo anotaba.
        ({"harness": "1"}, False),
        ({"harness.pid": "no-es-un-numero", "harness.host": "pc"}, False),
    ],
)
def test_es_de_ejecucion_viva(etiquetas: dict[str, str], es_viva: bool) -> None:
    assert es_de_ejecucion_viva(etiquetas, host="pc", vivo=lambda pid: pid == 10) is es_viva


def test_etiquetas_ejecucion_y_argumentos() -> None:
    etiquetas = etiquetas_ejecucion("tareas-api", "TAREAS-1", "abc123", es_conservar=True)
    assert etiquetas["harness"] == "1"
    assert etiquetas["harness.pid"] == str(os.getpid())
    assert etiquetas["harness.proyecto"] == "tareas-api"
    assert etiquetas["harness.ticket"] == "TAREAS-1"
    assert etiquetas["harness.ejecucion"] == "abc123"
    assert etiquetas["harness.conservar"] == "1"
    assert "harness.conservar" not in etiquetas_ejecucion("p", "t", "e")
    assert argumentos_etiquetas({"a": "1", "b": "x=y"}) == ["--label", "a=1", "--label", "b=x=y"]


class EjecutorFalso:
    def __init__(self, *respuestas: tuple[int, str]) -> None:
        self.respuestas = list(respuestas)
        self.llamadas: list[list[str]] = []

    def __call__(self, argumentos: list[str], timeout: float) -> tuple[int, str]:
        self.llamadas.append(argumentos)
        return self.respuestas.pop(0)


def test_listar_contenedores_por_etiqueta() -> None:
    inspeccion = [{
        "Id": "abc", "Name": "/harness-x", "Config": {"Labels": {"harness": "1"}},
        "State": {"Running": True},
        "Mounts": [{"Type": "volume", "Name": "harness-cache-x"}, {"Type": "bind"}],
    }]
    ejecutar = EjecutorFalso((0, "abc\n"), (0, json.dumps(inspeccion)))
    (recurso,) = listar(ejecutar, CONTENEDOR, ["label=harness=1"])
    assert ejecutar.llamadas == [
        ["docker", "ps", "-a", "-q", "--no-trunc", "--filter", "label=harness=1"],
        ["docker", "inspect", "abc"],
    ]
    assert recurso.nombre == "harness-x" and recurso.es_corriendo
    assert recurso.volumenes == ("harness-cache-x",)


def test_listar_volumenes_y_redes() -> None:
    volumen = [{"Name": "harness-cache-x", "Labels": {"harness.cache": "x"}}]
    (recurso,) = listar(EjecutorFalso((0, "v\n"), (0, json.dumps(volumen))), VOLUMEN, [])
    assert recurso.id == "harness-cache-x" and recurso.etiquetas == {"harness.cache": "x"}
    red = [{"Id": "n1", "Name": "harness-red", "Labels": None}]
    (recurso,) = listar(EjecutorFalso((0, "n1\n"), (0, json.dumps(red))), RED, [])
    assert recurso.id == "n1" and recurso.etiquetas == {}


@pytest.mark.parametrize(
    "respuestas",
    [
        [(1, "error")],
        [(0, "")],
        [(0, "abc"), (1, "")],
        [(0, "abc"), (0, "no es json")],
        [(0, "abc"), (0, "{}")],
    ],
)
def test_si_docker_falla_la_lista_sale_vacia(respuestas: list[tuple[int, str]]) -> None:
    # Quien usa la lista para borrar, no borra nada.
    assert listar(EjecutorFalso(*respuestas), CONTENEDOR, []) == []
