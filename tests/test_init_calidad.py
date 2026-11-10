"""harness init: imágenes ya descargadas, advertencias de límites y testLogging de Gradle."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli, entorno_docker
from harness.config import ConfigModelo
from harness.config_proyecto import validar_texto_config
from harness.entorno_docker import ErrorDocker, ResultadoComando, listar_imagenes
from harness.init import (
    ErrorInit,
    advertencias_de_limites,
    crear_herramientas_guardar,
    ejecutar_init,
    recomendacion_arquitectura,
    sugerencia_test_logging,
)
from harness.herramientas.archivos import ErrorHerramienta
from harness.tipos import LlamadaHerramienta
from tests.falsos import ProveedorFalso, pedir, terminar

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


# --- init --verificar con corrección -----------------------------------------------------

PYTHON_ROTO = PYTHON.replace("pip install -e .", "pip install -r requirements.txt")


def _guardar(herramienta: str, contenido: str, id_llamada: str) -> object:
    return pedir(LlamadaHerramienta(id_llamada, herramienta, {"contenido": contenido}))


def _init_verificar(
    monkeypatch: pytest.MonkeyPatch, repo: Path, respuestas: list, verificaciones: list
) -> tuple[int, str, ProveedorFalso, list[str]]:
    proveedor = ProveedorFalso(respuestas)
    config = ConfigModelo("gemini", "m", None, "clave-falsa-0123456789")
    monkeypatch.setattr(cli, "cargar_config", lambda **_: config)
    monkeypatch.setattr(cli, "crear_proveedor", lambda *_: proveedor)
    instalados: list[str] = []

    def verificar_falso(repo, config, variables, al_avanzar=None, **opciones):
        instalados.append(config.docker.instalar[0])
        resultado = verificaciones.pop(0)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    monkeypatch.setattr(cli, "verificar_config", verificar_falso)
    resultado = CliRunner().invoke(cli.app, ["init", "--repo", str(repo), "--verificar"])
    return resultado.exit_code, " ".join(resultado.output.split()), proveedor, instalados


def test_si_la_instalacion_falla_el_modelo_corrige_y_se_verifica_de_nuevo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text("# Reglas\n", encoding="utf-8")
    falla = ErrorDocker("Falló la instalación 'pip install -r requirements.txt': No such file")
    respuestas = [
        _guardar("guardar_harness_yaml", PYTHON_ROTO, "g1"),
        _guardar("guardar_harness_yaml", PYTHON, "g2"),
    ]
    codigo, salida, proveedor, instalados = _init_verificar(
        monkeypatch, tmp_path, respuestas, [falla, ResultadoComando(0, "3 passed")]
    )
    assert codigo == 0, salida
    assert instalados == ["pip install -r requirements.txt", "pip install -e ."]
    assert "Se le devuelve el error al modelo para que corrija harness.yaml (1/2)." in salida
    assert "✓ Verificación: la instalación y los tests funcionan." in salida
    pedido = proveedor.conversaciones[1][-1].contenido
    assert "No such file" in pedido and "responde con texto explicando por qué" in pedido


def test_si_el_modelo_dice_que_el_yaml_esta_bien_se_termina(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text("# Reglas\n", encoding="utf-8")
    respuestas = [
        _guardar("guardar_harness_yaml", PYTHON, "g1"),
        terminar("El YAML está bien: el test test_total falla en el código."),
    ]
    codigo, salida, _, instalados = _init_verificar(
        monkeypatch, tmp_path, respuestas, [ResultadoComando(1, "FAILED test_total")]
    )
    assert codigo == 1 and len(instalados) == 1
    assert "El modelo no cambió harness.yaml: El YAML está bien" in salida


def test_como_mucho_dos_correcciones(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Reglas\n", encoding="utf-8")
    respuestas = [_guardar("guardar_harness_yaml", PYTHON, f"g{n}") for n in range(3)]
    fallas = [ResultadoComando(1, "FAILED") for _ in range(3)]
    codigo, salida, _, instalados = _init_verificar(monkeypatch, tmp_path, respuestas, fallas)
    assert codigo == 1 and len(instalados) == 3
    assert "(2/2)" in salida and "(3/2)" not in salida


def test_un_harness_yaml_que_ya_existia_no_se_corrige(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text("# Reglas\n", encoding="utf-8")
    (tmp_path / "harness.yaml").write_text(PYTHON, encoding="utf-8")
    codigo, salida, proveedor, instalados = _init_verificar(
        monkeypatch, tmp_path, [], [ResultadoComando(1, "FAILED")]
    )
    assert codigo == 1 and len(instalados) == 1
    assert proveedor.conversaciones == [] and "corrija" not in salida


# --- Reglas de arquitectura --------------------------------------------------------------


@pytest.mark.parametrize(
    ("archivo", "stack", "herramienta"),
    [
        ("pom.xml", "Maven", "ArchUnit"),
        ("build.gradle.kts", "Gradle", "ArchUnit"),
        ("pyproject.toml", "Python", "import-linter"),
        ("package.json", "Node", "dependency-cruiser"),
    ],
)
def test_recomienda_segun_el_stack(
    tmp_path: Path, archivo: str, stack: str, herramienta: str
) -> None:
    (tmp_path / archivo).write_text("{}\n", encoding="utf-8")
    assert recomendacion_arquitectura(tmp_path)[0] == stack
    assert herramienta in recomendacion_arquitectura(tmp_path)[1]


def test_no_recomienda_si_ya_usa_una_o_no_hay_stack(tmp_path: Path) -> None:
    assert recomendacion_arquitectura(tmp_path) is None
    (tmp_path / "pom.xml").write_text("<artifactId>archunit-junit5</artifactId>\n",
                                      encoding="utf-8")
    assert recomendacion_arquitectura(tmp_path) is None


def test_init_muestra_la_recomendacion(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Reglas\n", encoding="utf-8")
    (tmp_path / "harness.yaml").write_text(PYTHON, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    resultado = CliRunner().invoke(cli.app, ["init", "--repo", str(tmp_path)])
    salida = " ".join(resultado.output.split())
    assert "Reglas de arquitectura (Python): para que la validación las haga cumplir" in salida
    assert (
        "  https://github.com/GersonPoma/harness/blob/main/docs/harness-yaml.md"
        "#reglas-de-arquitectura\n" in resultado.output
    )
