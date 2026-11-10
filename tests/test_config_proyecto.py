from pathlib import Path

import pytest

from harness.config_proyecto import (
    ErrorConfigProyecto,
    cargar_config_proyecto,
    validar_texto_config,
)

MINIMO = """\
version: 1
proyecto: tareas-api
docker:
  imagen: python:3.12-slim
comandos:
  tests: pytest -q
"""

COMPLETO = """\
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
servicios:
  - nombre: db
    imagen: postgres:16
    variables:
      POSTGRES_PASSWORD: test
    listo_cuando: pg_isready -h 127.0.0.1 -U postgres
variables_entorno:
  DATABASE_URL: postgresql://postgres:test@db:5432/app_test
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


def test_harness_yaml_con_bom(tmp_path: Path) -> None:
    (tmp_path / "harness.yaml").write_text(MINIMO, encoding="utf-8-sig")
    assert cargar_config_proyecto(tmp_path).proyecto == "tareas-api"


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
    config = _cargar(tmp_path, COMPLETO)
    assert len(config.docker.instalar) == 2
    assert config.limites.max_pasos == 10
    assert config.limites.timeout_comando_segundos == 120
    assert config.git.rama_base == "develop"
    assert config.archivos_protegidos == ["migrations/**"]
    assert config.servicios[0].variables == {"POSTGRES_PASSWORD": "test"}
    assert config.servicios[0].timeout_listo_segundos == 60
    assert config.variables_entorno["DATABASE_URL"].endswith("@db:5432/app_test")


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


def test_max_pasos_plan_por_defecto_y_explicito(tmp_path: Path) -> None:
    assert _cargar(tmp_path, MINIMO).limites.max_pasos_plan == 10
    # Su propio presupuesto: puede ser mayor que max_pasos, que cuenta desde que se aprueba.
    limites = "limites:\n  max_pasos: 5\n  max_pasos_plan: 20\n"
    assert _cargar(tmp_path, MINIMO + limites).limites.max_pasos_plan == 20


def test_max_pasos_plan_menor_que_1_es_error(tmp_path: Path) -> None:
    assert "max_pasos_plan" in _error(tmp_path, MINIMO + "limites:\n  max_pasos_plan: 0\n")


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


def test_yaml_invalido(tmp_path: Path) -> None:
    assert "no es YAML válido" in _error(tmp_path, "version: [1\n")


def test_yaml_que_no_es_objeto(tmp_path: Path) -> None:
    assert "debe ser un objeto" in _error(tmp_path, "- a\n- b\n")


def test_no_ejecuta_codigo_del_yaml(tmp_path: Path) -> None:
    # yaml.load con estas etiquetas ejecutaría código; safe_load las rechaza.
    error = _error(tmp_path, "!!python/object/apply:os.system ['echo hackeado']\n")
    assert "no es YAML válido" in error


# --- docker.cache -------------------------------------------------------------------------


def test_cache_activada_por_defecto(tmp_path: Path) -> None:
    assert _cargar(tmp_path, MINIMO).docker.cache is True


def test_cache_desactivada(tmp_path: Path) -> None:
    contenido = MINIMO.replace("  imagen: python:3.12-slim", "  imagen: x\n  cache: false")
    assert _cargar(tmp_path, contenido).docker.cache is False


def test_cache_tiene_que_ser_booleano(tmp_path: Path) -> None:
    contenido = MINIMO.replace("  imagen: python:3.12-slim", "  imagen: x\n  cache: 'no'")
    assert "docker.cache" in _error(tmp_path, contenido)


def test_timeout_descarga_por_defecto_y_explicito(tmp_path: Path) -> None:
    assert _cargar(tmp_path, MINIMO).docker.timeout_descarga_segundos == 600
    contenido = MINIMO.replace(
        "  imagen: python:3.12-slim", "  imagen: x\n  timeout_descarga_segundos: 1800"
    )
    assert _cargar(tmp_path, contenido).docker.timeout_descarga_segundos == 1800


@pytest.mark.parametrize("valor", ["0", "'600'", "-5"])
def test_timeout_descarga_invalido(tmp_path: Path, valor: str) -> None:
    contenido = MINIMO.replace(
        "  imagen: python:3.12-slim", f"  imagen: x\n  timeout_descarga_segundos: {valor}"
    )
    assert "docker.timeout_descarga_segundos" in _error(tmp_path, contenido)


def test_usuario_host_desactivado_por_defecto(tmp_path: Path) -> None:
    assert _cargar(tmp_path, MINIMO).docker.usuario_host is False
    contenido = MINIMO.replace("  imagen: python:3.12-slim", "  imagen: x\n  usuario_host: true")
    assert _cargar(tmp_path, contenido).docker.usuario_host is True


def test_usuario_host_tiene_que_ser_booleano(tmp_path: Path) -> None:
    contenido = MINIMO.replace("  imagen: python:3.12-slim", "  imagen: x\n  usuario_host: 'si'")
    assert "docker.usuario_host" in _error(tmp_path, contenido)


@pytest.mark.parametrize("directorio", ["/harness-cache", "/harness-cache/app", "/"])
def test_directorio_de_trabajo_no_puede_pisar_la_cache(tmp_path: Path, directorio: str) -> None:
    contenido = MINIMO.replace(
        "  imagen: python:3.12-slim", f"  imagen: x\n  directorio_trabajo: {directorio}"
    )
    error = _error(tmp_path, contenido)
    assert "docker.directorio_trabajo" in error
    assert "no puede coincidir con la caché de dependencias (/harness-cache)" in error


def test_directorio_de_trabajo_con_nombre_parecido_vale(tmp_path: Path) -> None:
    contenido = MINIMO.replace(
        "  imagen: python:3.12-slim", "  imagen: x\n  directorio_trabajo: /harness-cache-app"
    )
    assert _cargar(tmp_path, contenido).docker.directorio_trabajo == "/harness-cache-app"


def test_max_caracteres_conversacion(tmp_path: Path) -> None:
    assert _cargar(tmp_path, MINIMO).limites.max_caracteres_conversacion == 200_000
    contenido = MINIMO + "limites:\n  max_caracteres_conversacion: 80000\n"
    assert _cargar(tmp_path, contenido).limites.max_caracteres_conversacion == 80_000
    assert "limites.max_caracteres_conversacion" in _error(
        tmp_path, MINIMO + "limites:\n  max_caracteres_conversacion: 0\n"
    )


# --- jira ---------------------------------------------------------------------------------


def test_jira_estado_al_abrir_pr() -> None:
    base = "version: 1\nproyecto: p\ndocker:\n  imagen: python:3.12\ncomandos:\n  tests: pytest\n"
    assert validar_texto_config(base).jira.estado_al_abrir_pr is None
    config = validar_texto_config(base + "jira:\n  estado_al_abrir_pr: En revisión\n")
    assert config.jira.estado_al_abrir_pr == "En revisión"
    with pytest.raises(ErrorConfigProyecto, match="estado_al_abrir_pr"):
        validar_texto_config(base + "jira:\n  estado_al_abrir_pr: '  '\n")
    with pytest.raises(ErrorConfigProyecto, match="jira.otra"):
        validar_texto_config(base + "jira:\n  otra: 1\n")
