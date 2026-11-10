"""harness limpiar: solo lo del harness (por etiqueta), nunca lo de una ejecución viva."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness import etiquetas as modulo_etiquetas
from harness.limpieza import (
    IMAGEN,
    Elemento,
    PlanLimpieza,
    ResultadoLimpieza,
    borrar,
    bytes_de,
    planear,
)

HOST = "pc"
PID_VIVO = 100


@pytest.fixture(autouse=True)
def solo_el_pid_100_vive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo_etiquetas, "host_actual", lambda: HOST)
    monkeypatch.setattr(modulo_etiquetas, "proceso_vivo", lambda pid: pid == PID_VIVO)


def _etiquetas(pid: int, **otras: str) -> dict[str, str]:
    return {"harness": "1", "harness.pid": str(pid), "harness.host": HOST, **otras}


CONTENEDORES = [
    {"Id": "c-viva", "Name": "/harness-tareas-api-t-1-aaaaaa", "SizeRw": 1000,
     "Config": {"Labels": _etiquetas(PID_VIVO, **{"harness.proyecto": "tareas-api"})},
     "State": {"Running": True}, "Mounts": [{"Type": "volume", "Name": "harness-cache-b"}]},
    {"Id": "c-muerta", "Name": "/harness-tareas-api-init-bbbbbb", "SizeRw": 2_000_000,
     "Config": {"Labels": _etiquetas(999, **{"harness.proyecto": "tareas-api",
                                             "harness.ticket": "init"})},
     "State": {"Running": True}, "Mounts": []},
    {"Id": "c-conservada", "Name": "/harness-tienda-api-t-2-cccccc", "SizeRw": 3_000_000,
     "Config": {"Labels": _etiquetas(998, **{"harness.conservar": "1"})},
     "State": {"Running": False}, "Mounts": []},
]
REDES = [
    {"Id": "n-muerta", "Name": "harness-notas-api-n-1-dddddd", "Labels": _etiquetas(999)},
    {"Id": "n-viva", "Name": "harness-notas-api-n-2-eeeeee", "Labels": _etiquetas(PID_VIVO)},
]
VOLUMENES = [
    {"Name": "harness-cache-a", "Labels": {"harness": "1", "harness.cache": "a"}},
    {"Name": "harness-cache-b", "Labels": {"harness": "1", "harness.cache": "b"}},
]
DF = [{"Name": "harness-cache-a", "Size": "82.38MB"}, {"Name": "otro", "Size": "1GB"}]


class DockerFalso:
    """Responde cada comando de docker por su texto exacto; lo demás, vacío."""

    def __init__(self, respuestas: dict[str, tuple[int, str]]) -> None:
        self.respuestas = respuestas
        self.llamadas: list[str] = []

    def __call__(self, argumentos: list[str], timeout: float) -> tuple[int, str]:
        comando = " ".join(argumentos)
        self.llamadas.append(comando)
        return self.respuestas.get(comando, (0, ""))


FILTRO = "--filter label=harness=1"
RESPUESTAS = {
    f"docker ps -a -q --no-trunc {FILTRO}": (0, "c-viva\nc-muerta\nc-conservada\n"),
    "docker inspect --size c-viva c-muerta c-conservada": (0, json.dumps(CONTENEDORES)),
    f"docker network ls -q --no-trunc {FILTRO}": (0, "n-muerta n-viva"),
    "docker network inspect n-muerta n-viva": (0, json.dumps(REDES)),
    f"docker volume ls -q {FILTRO} --filter label=harness.cache": (
        0, "harness-cache-a\nharness-cache-b\n"
    ),
    "docker volume inspect harness-cache-a harness-cache-b": (0, json.dumps(VOLUMENES)),
    "docker system df -v --format {{json .Volumes}}": (0, json.dumps(DF)),
    "docker ps -a -q --no-trunc --filter volume=harness-cache-b": (0, "c-viva\n"),
    "docker image inspect --format {{.Id}} {{.Size}} python:3.12-slim": (0, "sha256:x 150000000"),
    "docker image inspect --format {{.Id}} {{.Size}} postgres:16": (0, "sha256:y 400000000"),
    "docker image inspect --format {{.Id}} {{.Size}} redis:7": (1, "No such image"),
    "docker ps -a -q --no-trunc --filter ancestor=python:3.12-slim": (0, "c-muerta\n"),
    "docker ps -a -q --no-trunc --filter ancestor=postgres:16": (0, "c-de-otro-proyecto\n"),
}


def _nombres(elementos: list[Elemento]) -> list[str]:
    return [e.nombre for e in elementos]


def test_sin_opciones_solo_contenedores_y_redes_huerfanos() -> None:
    docker = DockerFalso(RESPUESTAS)
    plan = planear(docker)
    assert _nombres(plan.a_borrar) == [
        "harness-tareas-api-init-bbbbbb", "harness-tienda-api-t-2-cccccc",
        "harness-notas-api-n-1-dddddd",
    ]
    omitidos = {e.nombre: motivo for e, motivo in plan.omitidos}
    assert omitidos == {
        "harness-tareas-api-t-1-aaaaaa": "es de una ejecución que sigue corriendo",
        "harness-notas-api-n-2-eeeeee": "es de una ejecución que sigue corriendo",
    }
    assert plan.total_bytes == 5_000_000
    # Sin --cache no se lista ni un volumen; sin --imagenes, ni una imagen.
    assert not any("volume" in c or "image" in c for c in docker.llamadas)
    # Todo se listó por etiqueta.
    listados = [c for c in docker.llamadas if " ls " in c or c.startswith("docker ps")]
    assert all(FILTRO in c for c in listados)


def test_el_detalle_dice_de_quien_es() -> None:
    plan = planear(DockerFalso(RESPUESTAS))
    detalles = {e.nombre: e.detalle for e in plan.a_borrar}
    assert detalles["harness-tareas-api-init-bbbbbb"] == "tareas-api / init, corriendo"
    assert detalles["harness-tienda-api-t-2-cccccc"] == "conservado, detenido"


def test_con_cache_los_volumenes_con_etiqueta_salvo_uno_en_uso() -> None:
    plan = planear(DockerFalso(RESPUESTAS), con_cache=True)
    (volumen,) = [e for e in plan.a_borrar if e.tipo == "volumen"]
    assert volumen.nombre == "harness-cache-a" and volumen.bytes == 82_380_000
    assert ("harness-cache-b", "lo usa 1 contenedor") in [
        (e.nombre, m) for e, m in plan.omitidos
    ]


def test_con_cache_de_un_proyecto() -> None:
    docker = DockerFalso(RESPUESTAS)
    planear(docker, con_cache=True, proyecto_cache="tareas-api")
    assert f"docker volume ls -q {FILTRO} --filter label=harness.cache=tareas-api" in (
        docker.llamadas
    )


def test_imagenes_solo_las_del_yaml_que_ningun_contenedor_usa() -> None:
    plan = planear(DockerFalso(RESPUESTAS), imagenes=["python:3.12-slim", "postgres:16", "redis:7"])
    imagenes = [e for e in plan.a_borrar if e.tipo == IMAGEN]
    # python la usa un contenedor que también se borra: se puede. postgres la usa uno ajeno.
    assert _nombres(imagenes) == ["python:3.12-slim"]
    assert imagenes[0].bytes == 150_000_000
    assert ("postgres:16", "lo usa 1 contenedor") in [(e.nombre, m) for e, m in plan.omitidos]
    # redis no está descargada: no aparece.
    assert "redis:7" not in _nombres(plan.a_borrar) + [e.nombre for e, _ in plan.omitidos]


def test_si_no_se_puede_saber_si_un_volumen_esta_en_uso_no_se_borra() -> None:
    respuestas = {**RESPUESTAS, "docker ps -a -q --no-trunc --filter volume=harness-cache-a": (
        1, "error"
    )}
    plan = planear(DockerFalso(respuestas), con_cache=True)
    assert not [e for e in plan.a_borrar if e.tipo == "volumen"]


def test_el_filtro_extra_limita_lo_que_se_ve() -> None:
    docker = DockerFalso(RESPUESTAS)
    planear(docker, filtro_extra=["label=harness.proyecto=prueba-1234"])
    assert docker.llamadas[0] == (
        f"docker ps -a -q --no-trunc {FILTRO} --filter label=harness.proyecto=prueba-1234"
    )


def test_borrar_por_id_en_orden_y_reporta_fallas() -> None:
    plan = PlanLimpieza([
        Elemento(IMAGEN, "python:3.12-slim", "python:3.12-slim", bytes=10),
        Elemento("volumen", "harness-cache-a", "harness-cache-a", bytes=5),
        Elemento("red", "n1", "harness-red"),
        Elemento("contenedor", "c1", "harness-c", bytes=1),
    ])
    docker = DockerFalso({"docker network rm n1": (1, "network has active endpoints")})
    resultado = borrar(docker, plan)
    assert docker.llamadas == [
        "docker rm -f -v c1", "docker network rm n1", "docker volume rm harness-cache-a",
        "docker rmi python:3.12-slim",
    ]
    assert resultado.bytes_liberados == 16
    assert [(e.id, error) for e, error in resultado.fallidos] == [
        ("n1", "network has active endpoints")
    ]


@pytest.mark.parametrize(
    ("texto", "bytes_"),
    [("82.38MB", 82_380_000), ("0B", 0), ("1.5GB", 1_500_000_000), ("12kB", 12_000), ("x", None)],
)
def test_bytes_de(texto: str, bytes_: int | None) -> None:
    assert bytes_de(texto) == bytes_


# --- harness limpiar ----------------------------------------------------------------------

PLAN = PlanLimpieza(
    [Elemento("contenedor", "c1", "harness-tareas-api-init-bbbbbb", "tareas-api", 2_000_000)],
    [(Elemento("contenedor", "c2", "harness-vivo"), "es de una ejecución que sigue corriendo")],
)


def _limpiar(
    monkeypatch: pytest.MonkeyPatch,
    *opciones: str,
    interactiva: bool = False,
    entrada: str | None = None,
    plan: PlanLimpieza = PLAN,
) -> tuple[int, str, list, list]:
    planes: list = []
    borrados: list = []

    def planear_falso(_ejecutar, *argumentos) -> PlanLimpieza:
        planes.append(argumentos)
        return plan

    def borrar_falso(_ejecutar, recibido: PlanLimpieza) -> ResultadoLimpieza:
        borrados.append(recibido)
        return ResultadoLimpieza(list(recibido.a_borrar))

    monkeypatch.setattr(cli, "planear", planear_falso)
    monkeypatch.setattr(cli, "borrar", borrar_falso)
    monkeypatch.setattr(cli.consola, "es_terminal_interactiva", lambda: interactiva)
    resultado = CliRunner().invoke(cli.app, ["limpiar", *opciones], input=entrada)
    return resultado.exit_code, " ".join(resultado.output.split()), planes, borrados


def test_muestra_lo_que_borra_con_su_tamano_y_lo_que_deja(monkeypatch: pytest.MonkeyPatch) -> None:
    _, salida, _, _ = _limpiar(monkeypatch, "--si")
    assert "Se va a borrar:" in salida
    assert "harness-tareas-api-init-bbbbbb (tareas-api) 2.0 MB" in salida
    assert "Total: 1 elemento(s), 2.0 MB." in salida
    assert "harness-vivo" in salida and "sigue corriendo" in salida


def test_sin_terminal_y_sin_si_no_borra_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida, _, borrados = _limpiar(monkeypatch, entrada="s\n")
    assert codigo == 1 and borrados == []
    assert "harness limpiar --si" in salida


def test_con_si_borra_sin_preguntar(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida, _, borrados = _limpiar(monkeypatch, "--si")
    assert codigo == 0 and len(borrados) == 1
    assert "Borrado(s) 1 elemento(s)" in salida and "¿Borrar" not in salida


@pytest.mark.parametrize(("respuesta", "es_borrado"), [("s\n", True), ("n\n", False), ("\n", False)])
def test_en_la_terminal_pregunta(
    monkeypatch: pytest.MonkeyPatch, respuesta: str, es_borrado: bool
) -> None:
    codigo, salida, _, borrados = _limpiar(monkeypatch, interactiva=True, entrada=respuesta)
    assert codigo == 0 and bool(borrados) is es_borrado
    if not es_borrado:
        assert "No se borró nada." in salida


def test_sin_nada_que_borrar_no_pregunta(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida, _, borrados = _limpiar(monkeypatch, interactiva=True, plan=PlanLimpieza())
    assert codigo == 0 and borrados == [] and "No hay nada que borrar." in salida


def test_proyecto_va_con_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida, planes, _ = _limpiar(monkeypatch, "--proyecto", "tareas-api")
    assert codigo == 1 and planes == [] and "--proyecto va con --cache" in salida


def test_cache_y_proyecto_llegan_al_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, planes, _ = _limpiar(monkeypatch, "--cache", "--proyecto", "tareas-api", "--si")
    assert planes == [(True, "tareas-api", None)]


def test_imagenes_salen_del_harness_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "harness.yaml").write_text(
        "version: 1\nproyecto: demo\ndocker:\n  imagen: python:3.12-slim\ncomandos:\n  tests: x\n"
        "servicios:\n  - nombre: db\n    imagen: postgres:16\n",
        encoding="utf-8",
    )
    plan = PlanLimpieza([Elemento(IMAGEN, "python:3.12-slim", "python:3.12-slim", bytes=1)])
    _, salida, planes, _ = _limpiar(
        monkeypatch, "--imagenes", "--repo", str(tmp_path), "--si", plan=plan
    )
    assert planes == [(False, None, ["python:3.12-slim", "postgres:16"])]
    assert "pueden estar compartidas con otros proyectos" in salida


def test_imagenes_sin_harness_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    codigo, salida, planes, _ = _limpiar(monkeypatch, "--imagenes", "--repo", str(tmp_path))
    assert codigo == 1 and planes == [] and "no tiene uno" in salida
