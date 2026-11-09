"""harness init: imágenes ya descargadas, advertencias de límites y testLogging de Gradle."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli, entorno_docker
from harness.config_proyecto import validar_texto_config
from harness.entorno_docker import ErrorDocker, listar_imagenes
from harness.init import (
    ErrorInit,
    advertencias_de_limites,
    crear_herramientas_guardar,
    ejecutar_init,
    sugerencia_test_logging,
)
from harness.herramientas.archivos import ErrorHerramienta
from tests.falsos import ProveedorFalso, terminar

BASE = "version: 1\nproyecto: lib\ndocker:\n  imagen: {imagen}\n  instalar:\n    - {instalar}\n"
GRADLE = BASE.format(imagen="gradle:8.14-jdk21", instalar="gradle testClasses") + (
    "comandos:\n  tests: gradle test\n"
)
GRADLE_BIEN = GRADLE + "limites:\n  timeout_comando_segundos: 1200\n  max_caracteres_salida: 6000\n"
MAVEN = BASE.format(imagen="maven:3.9-eclipse-temurin-21", instalar="mvn -q compile") + (
    "comandos:\n  tests: mvn test\n"
)
PYTHON = BASE.format(imagen="python:3.12-slim", instalar="pip install -e .") + (
    "comandos:\n  tests: pytest\n"
)


# --- Imágenes ya descargadas -------------------------------------------------------------


def test_listar_imagenes(monkeypatch: pytest.MonkeyPatch) -> None:
    salida = "gradle:8.14-jdk21\npostgres:16\n<none>:<none>\npostgres:16\n"
    monkeypatch.setattr(entorno_docker, "ejecutar_proceso", lambda *_: (0, salida))
    assert listar_imagenes() == ["gradle:8.14-jdk21", "postgres:16"]


def test_sin_docker_no_hay_imagenes(monkeypatch: pytest.MonkeyPatch) -> None:
    def sin_docker(*_):
        raise ErrorDocker("No se encontró el comando 'docker'")

    monkeypatch.setattr(entorno_docker, "ejecutar_proceso", sin_docker)
    assert listar_imagenes() == []
    monkeypatch.setattr(entorno_docker, "ejecutar_proceso", lambda *_: (1, "daemon caído"))
    assert listar_imagenes() == []


def test_init_le_dice_al_modelo_las_imagenes_descargadas(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Reglas\n", encoding="utf-8")
    proveedor = ProveedorFalso([terminar(), terminar(), terminar()])
    with pytest.raises(ErrorInit):
        ejecutar_init(proveedor, tmp_path, imagenes=["gradle:8.14-jdk21", "postgres:16"])
    pedido = proveedor.conversaciones[0][1].contenido
    assert "Imágenes de Docker ya descargadas en esta máquina: gradle:8.14-jdk21, postgres:16" in (
        pedido
    )
    assert "prefiérela" in pedido


# --- Advertencias de límites -------------------------------------------------------------


def test_gradle_con_los_limites_por_defecto() -> None:
    advertencias = advertencias_de_limites(validar_texto_config(GRADLE))
    assert len(advertencias) == 2
    assert "timeout_comando_segundos es 120: en Gradle se recomienda 1200" in advertencias[0]
    assert "max_caracteres_salida es 4000: en Gradle se recomienda 6000" in advertencias[1]


def test_gradle_con_no_daemon() -> None:
    yaml = GRADLE_BIEN.replace("tests: gradle test", "tests: gradle test --no-daemon")
    (advertencia,) = advertencias_de_limites(validar_texto_config(yaml))
    assert "--no-daemon hace más lento cada comando" in advertencia


@pytest.mark.parametrize(
    ("yaml", "cantidad"),
    [(GRADLE_BIEN, 0), (MAVEN, 2), (PYTHON, 0),
     (MAVEN + "limites:\n  timeout_comando_segundos: 600\n  max_caracteres_salida: 6000\n", 0)],
)
def test_advertencias_por_stack(yaml: str, cantidad: int) -> None:
    assert len(advertencias_de_limites(validar_texto_config(yaml))) == cantidad


def test_guardar_con_advertencias_no_guarda_hasta_que_el_modelo_insiste(tmp_path: Path) -> None:
    creados: list[str] = []
    avisos: list[str] = []
    (guardar,) = crear_herramientas_guardar(tmp_path, ["harness.yaml"], creados.append,
                                            avisos.append)
    with pytest.raises(ErrorHerramienta, match="No se guardó: hay límites por debajo"):
        guardar.funcion(contenido=GRADLE)
    assert not (tmp_path / "harness.yaml").exists() and creados == []

    guardar.funcion(contenido=GRADLE, ignorar_advertencias=True)
    assert (tmp_path / "harness.yaml").exists() and creados == ["harness.yaml"]
    assert avisos[0].startswith("harness.yaml se guardó con advertencias (el modelo las ignoró)")
    assert "ignorar_advertencias" in guardar.parametros["properties"]


def test_guardar_sin_advertencias_guarda_a_la_primera(tmp_path: Path) -> None:
    (guardar,) = crear_herramientas_guardar(tmp_path, ["harness.yaml"], lambda _: None)
    guardar.funcion(contenido=GRADLE_BIEN)
    assert (tmp_path / "harness.yaml").exists()


# --- testLogging de Gradle ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("archivo", "esperado"),
    [("build.gradle", 'exceptionFormat "full"'), ("build.gradle.kts", "TestExceptionFormat.FULL")],
)
def test_sugiere_test_logging_si_falta(tmp_path: Path, archivo: str, esperado: str) -> None:
    (tmp_path / archivo).write_text("plugins { id 'java' }\n", encoding="utf-8")
    nombre, bloque = sugerencia_test_logging(tmp_path)
    assert nombre == archivo and esperado in bloque


def test_sin_sugerencia_si_ya_lo_tiene_o_no_es_gradle(tmp_path: Path) -> None:
    assert sugerencia_test_logging(tmp_path) is None
    (tmp_path / "build.gradle").write_text(
        "test { testLogging { exceptionFormat 'full' } }\n", encoding="utf-8"
    )
    assert sugerencia_test_logging(tmp_path) is None


def test_init_muestra_la_sugerencia_sin_tocar_el_build(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Reglas\n", encoding="utf-8")
    (tmp_path / "harness.yaml").write_text(GRADLE_BIEN, encoding="utf-8")
    build = "plugins { id 'java' }\n"
    (tmp_path / "build.gradle").write_text(build, encoding="utf-8")
    resultado = CliRunner().invoke(cli.app, ["init", "--repo", str(tmp_path)])
    salida = " ".join(resultado.output.split())
    assert resultado.exit_code == 0, resultado.output
    assert "build.gradle no configura testLogging con exceptionFormat FULL" in salida
    assert 'exceptionFormat "full"' in salida
    assert (tmp_path / "build.gradle").read_text(encoding="utf-8") == build
