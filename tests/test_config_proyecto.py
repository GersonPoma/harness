from pathlib import Path

import pytest

from harness.config_proyecto import ErrorConfigProyecto, cargar_config_proyecto

MINIMO = """\
version: 1
proyecto: tareas-api
docker:
  imagen: python:3.12-slim
comandos:
  tests: pytest -q
"""

COMPLETO_SIN_FASE_3 = """\
version: 1
proyecto: mi-api
docker:
  imagen: python:3.11-slim
  directorio_trabajo: /codigo
  instalar:
    - pip install -r requirements.txt
    - pip install -r requirements-dev.txt
  reinstalar_si_cambia:
    - requirements*.txt
comandos:
  tests: pytest -q
  lint: ruff check .
comandos_permitidos: [pytest, python]
git:
  rama_base: develop
limites:
  max_pasos: 10
archivos_protegidos:
  - migrations/**
"""


def _cargar(tmp_path: Path, contenido: str):
    (tmp_path / "harness.yaml").write_text(contenido, encoding="utf-8")
    return cargar_config_proyecto(tmp_path)


def _error(tmp_path: Path, contenido: str) -> str:
    with pytest.raises(ErrorConfigProyecto) as info:
        _cargar(tmp_path, contenido)
    return str(info.value)


def test_sin_harness_yaml_devuelve_none(tmp_path: Path) -> None:
    assert cargar_config_proyecto(tmp_path) is None


def test_minimo_con_valores_por_defecto(tmp_path: Path) -> None:
    config = _cargar(tmp_path, MINIMO)
    assert config.docker.directorio_trabajo == "/app"
    assert config.docker.instalar == []
    assert config.comandos.lint is None
    assert config.comandos_permitidos == []
    assert config.git.prefijo_rama == "feature/"
    assert config.limites.max_caracteres_salida == 4000


def test_completo(tmp_path: Path) -> None:
    config = _cargar(tmp_path, COMPLETO_SIN_FASE_3)
    assert len(config.docker.instalar) == 2
    assert config.limites.max_pasos == 10
    assert config.limites.timeout_comando_segundos == 120
    assert config.git.rama_base == "develop"
    assert config.archivos_protegidos == ["migrations/**"]


def test_instalar_como_texto_se_vuelve_lista(tmp_path: Path) -> None:
    config = _cargar(tmp_path, MINIMO.replace("python:3.12-slim", "x\n  instalar: pip install ."))
    assert config.docker.instalar == ["pip install ."]


def test_harness_yaml_de_tareas_api(tmp_path: Path) -> None:
    ruta = Path(__file__).parents[2] / "tareas-api" / "harness.yaml"
    if not ruta.is_file():
        pytest.skip("tareas-api no está junto al repo del harness")
    _cargar(tmp_path, ruta.read_text(encoding="utf-8"))


def test_clave_desconocida(tmp_path: Path) -> None:
    error = _error(tmp_path, MINIMO.replace("comandos:", "comando:"))
    assert "comando: clave desconocida" in error
    assert "comandos: falta este campo" in error


def test_clave_obligatoria_anidada(tmp_path: Path) -> None:
    error = _error(tmp_path, MINIMO.replace("  imagen: python:3.12-slim", "  instalar: x"))
    assert "docker.imagen: falta este campo" in error


def test_tipo_incorrecto(tmp_path: Path) -> None:
    error = _error(tmp_path, MINIMO + "limites:\n  max_pasos: '30'\n")
    assert "limites.max_pasos" in error


def test_version_no_soportada(tmp_path: Path) -> None:
    error = _error(tmp_path, MINIMO.replace("version: 1", "version: 2"))
    assert "version: versión de harness.yaml no soportada" in error


def test_nombre_de_proyecto_invalido(tmp_path: Path) -> None:
    error = _error(tmp_path, MINIMO.replace("tareas-api", "Tareas_API"))
    assert "proyecto: solo se permiten minúsculas" in error


def test_servicios_repetidos(tmp_path: Path) -> None:
    servicios = "servicios:\n  - {nombre: db, imagen: a}\n  - {nombre: db, imagen: b}\n"
    assert "servicios repetidos: db" in _error(tmp_path, MINIMO + servicios)


def test_servicio_con_error_indica_la_posicion(tmp_path: Path) -> None:
    servicios = "servicios:\n  - {nombre: db}\n"
    assert "servicios[0].imagen: falta este campo" in _error(tmp_path, MINIMO + servicios)


@pytest.mark.parametrize(
    "extra",
    ["servicios:\n  - {nombre: db, imagen: postgres:16}\n", "variables_entorno:\n  A: b\n"],
)
def test_lo_de_la_fase_3_se_rechaza(tmp_path: Path, extra: str) -> None:
    assert "se soporta desde la fase 3" in _error(tmp_path, MINIMO + extra)


def test_yaml_invalido(tmp_path: Path) -> None:
    assert "no es YAML válido" in _error(tmp_path, "version: [1\n")


def test_yaml_que_no_es_objeto(tmp_path: Path) -> None:
    assert "debe ser un objeto" in _error(tmp_path, "- a\n- b\n")


def test_no_ejecuta_codigo_del_yaml(tmp_path: Path) -> None:
    # yaml.load con estas etiquetas ejecutaría código; safe_load las rechaza.
    error = _error(tmp_path, "!!python/object/apply:os.system ['echo hackeado']\n")
    assert "no es YAML válido" in error
