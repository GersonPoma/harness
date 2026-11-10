import pytest

from harness.config_proyecto import ConfigProyecto
from harness.variables import (
    ErrorVariables,
    avisos_secretos_cortos,
    avisos_variables_cortas_yaml,
    ocultar_secretos,
    resolver_variables,
)


def _config(variables_entorno: dict[str, str], servicio: dict[str, str] | None = None):
    return ConfigProyecto.model_validate({
        "version": 1,
        "proyecto": "demo",
        "docker": {"imagen": "python:3.12-slim"},
        "comandos": {"tests": "pytest"},
        "servicios": [{"nombre": "db", "imagen": "postgres:16", "variables": servicio or {}}],
        "variables_entorno": variables_entorno,
    })


def test_valores_literales_quedan_igual_y_no_son_secretos() -> None:
    resueltas = resolver_variables(_config({"SECRET_KEY": "clave-de-prueba"}), {})
    assert resueltas.proyecto == {"SECRET_KEY": "clave-de-prueba"}
    assert resueltas.secretos == []


def test_referencia_completa_y_dentro_de_un_texto() -> None:
    config = _config(
        {"API": "${MI_API}", "URL": "postgresql://u:${CLAVE}@db/x"},
        servicio={"POSTGRES_PASSWORD": "${CLAVE}"},
    )
    resueltas = resolver_variables(config, {"MI_API": "abc123", "CLAVE": "s3creta"})
    assert resueltas.proyecto == {"API": "abc123", "URL": "postgresql://u:s3creta@db/x"}
    assert resueltas.servicios == {"db": {"POSTGRES_PASSWORD": "s3creta"}}
    assert resueltas.secretos == ["abc123", "s3creta"]


def test_avisa_de_todas_las_variables_que_faltan() -> None:
    config = _config({"A": "${FALTA_1}", "B": "${EXISTE}"}, servicio={"C": "x-${FALTA_2}"})
    with pytest.raises(ErrorVariables) as error:
        resolver_variables(config, {"EXISTE": "1"})
    mensaje = str(error.value)
    assert "variables_entorno.A: falta la variable de entorno FALTA_1" in mensaje
    assert "servicios[0].variables.C: falta la variable de entorno FALTA_2" in mensaje
    assert "EXISTE" not in mensaje


def test_variable_vacia_no_es_secreto() -> None:
    resueltas = resolver_variables(_config({"A": "${VACIA}"}), {"VACIA": ""})
    assert resueltas.proyecto == {"A": ""}
    assert resueltas.secretos == []


def test_ocultar_secretos_empieza_por_el_mas_largo() -> None:
    texto = "clave=secreto-01 y clave larga=secreto-0123456"
    secretos = ["secreto-01", "secreto-0123456", ""]
    assert ocultar_secretos(texto, secretos) == "clave=**** y clave larga=****"


# --- Secretos demasiado cortos -------------------------------------------------------------


def test_un_secreto_corto_no_se_oculta() -> None:
    # "abc" por error en el .env: ocultarlo rompería cualquier texto que lo contenga.
    texto = '{"clave": "abc"} y def abcdario(): token-largo-0123'
    assert ocultar_secretos(texto, ["abc", "token-largo-0123"]) == (
        '{"clave": "abc"} y def abcdario(): ****'
    )


def test_avisos_secretos_cortos() -> None:
    entorno = {"CORTO": "abc", "LARGO": "token-0123456789", "VACIO": "", "JUSTO": "12345678"}
    avisos = avisos_secretos_cortos(["CORTO", "LARGO", "VACIO", "NO_EXISTE", "JUSTO", "CORTO"],
                                    entorno)
    assert avisos == [
        "El valor de CORTO es demasiado corto para ser un token real: revisa CORTO (viene "
        "del entorno)."
    ]


def test_resolver_variables_anota_de_donde_salen_los_secretos() -> None:
    config = _config({"DB": "postgresql://u:${CLAVE_DB}@db/x", "FIJA": "valor"})
    resueltas = resolver_variables(config, {"CLAVE_DB": "secreto-0123456789"})
    assert resueltas.nombres_secretos == ["CLAVE_DB"]


def test_avisos_variables_cortas_yaml() -> None:
    entorno = {"CLAVE_DB": "test", "OTRA": "contraseña-larga-123"}
    assert avisos_variables_cortas_yaml(["CLAVE_DB", "OTRA"], entorno) == [
        "El valor de CLAVE_DB (harness.yaml) es corto y no se oculta en la consola ni en el "
        "log; si es un secreto real, usa uno de al menos 8 caracteres."
    ]
