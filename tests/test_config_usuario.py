import os
import tomllib
from pathlib import Path

import pytest

from harness import config_usuario
from harness.config_usuario import (
    ConfigUsuario,
    ErrorConfigUsuario,
    JiraUsuario,
    Perfil,
    a_toml,
    aviso_permisos,
    enmascarar,
    guardar_config_usuario,
    leer_config_usuario,
    ruta_config,
)

COMPLETA = ConfigUsuario(
    perfil="rapido",
    respaldo=["fuerte", "groq:openai/gpt-oss-120b"],
    revisor="fuerte",
    perfiles={
        "rapido": Perfil(proveedor="gemini", modelo="gemini-3.5-flash-lite"),
        "fuerte": Perfil(proveedor="openrouter", modelo="nvidia/nemotron:free"),
        "mio": Perfil(
            proveedor="personalizado", modelo="m", base_url="https://mi.api/v1",
            api_key_env="MI_API_KEY",
        ),
    },
    credenciales={"GEMINI_API_KEY": 'clave "rara" \\ con\ttab', "GITHUB_TOKEN": "ghp_x"},
    jira=JiraUsuario(url="https://sitio.atlassian.net", email="yo@correo"),
)


def test_sin_archivo_es_una_configuracion_vacia() -> None:
    assert leer_config_usuario() == ConfigUsuario()


def test_vive_en_la_carpeta_del_usuario_fuera_del_repo(tmp_path: Path) -> None:
    # El conftest la apunta a una carpeta temporal; de verdad es ~/.harness/config.toml.
    assert ruta_config() == config_usuario.CARPETA_USUARIO / "config.toml"


def test_guardar_y_leer_da_lo_mismo() -> None:
    ruta = guardar_config_usuario(COMPLETA)
    assert leer_config_usuario() == COMPLETA
    texto = ruta.read_text(encoding="utf-8")
    assert texto.startswith("# Lo escriben 'harness config' y 'harness perfil'.")
    assert tomllib.loads(texto)["perfiles"]["mio"]["api_key_env"] == "MI_API_KEY"
    # Sin temporales sueltos en la carpeta.
    assert [p.name for p in ruta.parent.iterdir()] == ["config.toml"]


def test_a_toml_en_orden_fijo() -> None:
    texto = a_toml(ConfigUsuario(
        perfil="a", perfiles={"a": Perfil(proveedor="groq", modelo="m")},
        credenciales={"GROQ_API_KEY": "k"},
    ))
    assert texto.splitlines()[1:] == [
        'perfil = "a"', "", "[perfiles.a]", 'proveedor = "groq"', 'modelo = "m"', "",
        "[credenciales]", 'GROQ_API_KEY = "k"',
    ]


@pytest.mark.skipif(os.name != "posix", reason="los permisos 0600 son de Linux y macOS")
def test_en_posix_el_archivo_es_solo_del_usuario() -> None:
    ruta = guardar_config_usuario(COMPLETA)
    assert ruta.stat().st_mode & 0o777 == 0o600
    assert ruta.parent.stat().st_mode & 0o777 == 0o700
    assert aviso_permisos() is None
    ruta.chmod(0o644)
    assert "lo pueden leer otros usuarios" in aviso_permisos()


@pytest.mark.parametrize(
    ("texto", "mensaje"),
    [
        ("perfil = 'x'\n", "no existe el perfil 'x'"),
        ("otra = 1\n", "otra: Extra inputs are not permitted"),
        ("[perfiles.a]\nproveedor = 'inventado'\nmodelo = 'm'\n", "proveedor desconocido"),
        ("[perfiles.a]\nproveedor = 'personalizado'\nmodelo = 'm'\n", "base_url y api_key_env"),
        ("[perfiles.a]\nproveedor = 'groq'\nmodelo = 'm'\nbase_url = 'u'\n",
         "solo para proveedor = personalizado"),
        ("[perfiles.Mal]\nproveedor = 'groq'\nmodelo = 'm'\n", "solo admite [a-z0-9_-]"),
        ("respaldo = ['nadie']\n", "no es un perfil ni proveedor:modelo"),
        ("revisor = 'nadie'\n", "revisor: 'nadie' no es un perfil"),
        ("[credenciales]\n'MAL NOMBRE' = 'x'\n", "no es un nombre de variable"),
        ("perfil = \n", "no es un TOML válido"),
    ],
)
def test_errores_de_validacion(texto: str, mensaje: str) -> None:
    ruta = ruta_config()
    ruta.parent.mkdir(parents=True)
    ruta.write_text(texto, encoding="utf-8")
    with pytest.raises(ErrorConfigUsuario, match=mensaje.replace("[", "\\[")):
        leer_config_usuario()


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("AIzaSyA-0123456789-x9Q2", "****x9Q2"), ("corto", "****"), ("123456789012", "****9012")],
)
def test_enmascarar_nunca_muestra_el_secreto(valor: str, esperado: str) -> None:
    assert enmascarar(valor) == esperado
