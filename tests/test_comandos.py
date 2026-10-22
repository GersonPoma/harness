from pathlib import Path

import pytest

from harness.config_proyecto import ConfigComandos, ConfigDocker, ConfigProyecto
from harness.entorno_docker import ResultadoComando
from harness.herramientas import crear_herramientas
from harness.herramientas.archivos import ErrorHerramienta
from harness.herramientas.comandos import (
    EjecutorComandos,
    ejecutar_comando,
    formatear_resultado,
    recortar_salida,
    validar_comando,
    validar_proyecto,
)
from harness.herramientas.protegidos import GuardiaProtegidos
from harness.herramientas.rutas import ErrorRuta
from tests.falsos import EntornoFalso

PERMITIDOS = ["pytest", "python", "ls"]


def _config(lint: str | None = None, permitidos: list[str] | None = None) -> ConfigProyecto:
    return ConfigProyecto(
        version=1,
        proyecto="demo",
        docker=ConfigDocker(imagen="python:3.12-slim"),
        comandos=ConfigComandos(tests="pytest -q", lint=lint),
        comandos_permitidos=PERMITIDOS if permitidos is None else permitidos,
        archivos_protegidos=["tickets/**"],
    )


def _ejecutor(
    repo: Path, resultados: list[ResultadoComando]
) -> tuple[EjecutorComandos, EntornoFalso]:
    entorno = EntornoFalso(resultados)
    return EjecutorComandos(entorno, GuardiaProtegidos(repo, []), 4000, 120), entorno


@pytest.mark.parametrize("comando", ["pytest -q", "pytest -k 'crear tarea'", "ls src", "python -V"])
def test_comandos_permitidos(comando: str) -> None:
    validar_comando(comando, PERMITIDOS)


@pytest.mark.parametrize(
    "comando",
    [
        "pytest; rm -rf /app",
        "pytest && rm -rf /app",
        "pytest || rm -rf /app",
        "pytest & rm -rf /app",
        "ls | sh",
        "python x.py > salida.txt",
        "python < entrada.txt",
        "python `rm -rf /app`",
        "python $(rm -rf /app)",
        "pytest\nrm -rf /app",
        "pytest\rrm -rf /app",
    ],
)
def test_rechaza_encadenar_o_redirigir(comando: str) -> None:
    with pytest.raises(ErrorHerramienta, match="encadenar"):
        validar_comando(comando, PERMITIDOS)


@pytest.mark.parametrize("comando", ["rm -rf /app", "pip install requests", "pytestx", "sh -c x"])
def test_rechaza_programas_no_permitidos(comando: str) -> None:
    with pytest.raises(ErrorHerramienta, match="no está permitido"):
        validar_comando(comando, PERMITIDOS)


def test_rechaza_comando_vacio() -> None:
    with pytest.raises(ErrorHerramienta, match="vacío"):
        validar_comando("   ", PERMITIDOS)


def test_recortar_salida_conserva_el_final() -> None:
    assert recortar_salida("abc", 10) == "abc"
    recortada = recortar_salida("0123456789ERROR", 5)
    assert recortada.endswith("\nERROR")
    assert "10 caracteres" in recortada


def test_formatear_resultado() -> None:
    fallido = formatear_resultado(ResultadoComando(1, "falló\n"), 100)
    assert fallido == "Código de salida: 1\nfalló"
    agotado = formatear_resultado(ResultadoComando(137, "", True), 100, timeout=120)
    assert agotado == "Tiempo agotado: el comando se cortó a los 120 s."


def test_ejecutar_comando_valida_antes_de_correr(repo: Path) -> None:
    ejecutor, entorno = _ejecutor(repo, [])
    with pytest.raises(ErrorHerramienta):
        ejecutar_comando(ejecutor, PERMITIDOS, "rm -rf /app")
    assert entorno.comandos == []


def test_validar_proyecto_con_tests_y_lint(repo: Path) -> None:
    ejecutor, entorno = _ejecutor(
        repo, [ResultadoComando(0, "3 passed"), ResultadoComando(1, "E501 línea larga")]
    )
    es_valido, informe = validar_proyecto(ejecutor, "pytest -q", "ruff check .")
    assert not es_valido
    assert entorno.comandos == ["pytest -q", "ruff check ."]
    assert "3 passed" in informe and "E501" in informe


def test_validar_proyecto_tiempo_agotado_no_pasa(repo: Path) -> None:
    ejecutor, _ = _ejecutor(repo, [ResultadoComando(0, "", es_tiempo_agotado=True)])
    assert validar_proyecto(ejecutor, "pytest -q", None)[0] is False


def _nombres(herramientas: list) -> list[str]:
    return [h.nombre for h in herramientas]


def test_herramientas_sin_harness_yaml(repo: Path) -> None:
    nombres = _nombres(crear_herramientas(repo))
    assert nombres == ["leer_archivo", "escribir_archivo", "listar_directorio"]


def test_herramientas_con_harness_yaml(repo: Path) -> None:
    ejecutor, _ = _ejecutor(repo, [])
    nombres = _nombres(crear_herramientas(repo, _config(lint="ruff check ."), ejecutor))
    assert nombres[3:] == ["ejecutar_tests", "ejecutar_lint", "ejecutar_comando"]


def test_sin_lint_ni_permitidos_no_se_ofrecen(repo: Path) -> None:
    ejecutor, _ = _ejecutor(repo, [])
    nombres = _nombres(crear_herramientas(repo, _config(permitidos=[]), ejecutor))
    assert nombres[3:] == ["ejecutar_tests"]


def test_escribir_respeta_archivos_protegidos_del_yaml(repo: Path) -> None:
    ejecutor, _ = _ejecutor(repo, [])
    escribir = crear_herramientas(repo, _config(), ejecutor)[1]
    with pytest.raises(ErrorRuta, match="protegido"):
        escribir.funcion(ruta="tickets/T-1.md", contenido="x")
    assert not (repo / "tickets").exists()


def test_el_modelo_no_puede_pisar_la_proteccion_con_argumentos(repo: Path) -> None:
    ejecutor, _ = _ejecutor(repo, [])
    escribir = crear_herramientas(repo, _config(), ejecutor)[1]
    with pytest.raises(TypeError):
        escribir.funcion(ruta="tickets/T-1.md", contenido="x", protegidos=[])
    assert not (repo / "tickets").exists()


def test_escribir_avisa_la_ruta_escrita(repo: Path) -> None:
    escritas: list[str] = []
    escribir = crear_herramientas(repo, al_escribir=escritas.append)[1]
    escribir.funcion(ruta="./pyproject.toml", contenido="[project]")
    assert escritas == ["pyproject.toml"]


def test_herramienta_ejecutar_comando(repo: Path) -> None:
    ejecutor, entorno = _ejecutor(repo, [ResultadoComando(0, "Python 3.12")])
    comando = crear_herramientas(repo, _config(), ejecutor)[-1]
    assert comando.funcion(comando="python -V") == "Código de salida: 0\nPython 3.12"
    assert entorno.comandos == ["python -V"]
