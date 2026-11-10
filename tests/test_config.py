import os
from pathlib import Path

import pytest

from harness.config import ErrorConfiguracion, cargar_config
from harness.proveedores.presets import PRESETS

VARIABLES = [
    "HARNESS_PROVEEDOR",
    "HARNESS_MODELO",
    "HARNESS_BASE_URL",
    "HARNESS_API_KEY_ENV",
    "MI_KEY",
    *(p.variable_api_key for p in PRESETS.values() if p.variable_api_key),
]


@pytest.fixture
def sin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Limpia el entorno y devuelve la ruta de un .env inexistente."""
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return tmp_path / ".env"


def test_config_completa_de_gemini(sin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_PROVEEDOR", "Gemini")
    monkeypatch.setenv("HARNESS_MODELO", "gemini-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa-0123456789")
    config = cargar_config(sin_env)
    assert config.proveedor == "gemini"
    assert config.base_url == PRESETS["gemini"].base_url
    assert config.api_key == "clave-falsa-0123456789"
    assert "clave-falsa-0123456789" not in repr(config)


def test_lee_desde_archivo_env(sin_env: Path) -> None:
    sin_env.write_text(
        "HARNESS_PROVEEDOR=groq\nHARNESS_MODELO=m\nGROQ_API_KEY=k\n", encoding="utf-8"
    )
    assert cargar_config(sin_env).proveedor == "groq"


def test_proveedor_desconocido(sin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_PROVEEDOR", "inventado")
    with pytest.raises(ErrorConfiguracion, match="desconocido.*gemini"):
        cargar_config(sin_env)


def test_falta_modelo(sin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_PROVEEDOR", "groq")
    with pytest.raises(ErrorConfiguracion, match="Falta HARNESS_MODELO"):
        cargar_config(sin_env)


def test_falta_api_key(sin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_PROVEEDOR", "openrouter")
    monkeypatch.setenv("HARNESS_MODELO", "m")
    with pytest.raises(ErrorConfiguracion, match="Falta OPENROUTER_API_KEY"):
        cargar_config(sin_env)


def test_personalizado(sin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_PROVEEDOR", "personalizado")
    monkeypatch.setenv("HARNESS_MODELO", "m")
    monkeypatch.setenv("HARNESS_BASE_URL", "https://mi.api/v1")
    monkeypatch.setenv("HARNESS_API_KEY_ENV", "MI_KEY")
    monkeypatch.setenv("MI_KEY", "clave-falsa-0123456789")
    config = cargar_config(sin_env)
    assert (config.base_url, config.api_key) == ("https://mi.api/v1", "clave-falsa-0123456789")


def test_personalizado_sin_base_url(sin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_PROVEEDOR", "personalizado")
    monkeypatch.setenv("HARNESS_MODELO", "m")
    with pytest.raises(ErrorConfiguracion, match="Falta HARNESS_BASE_URL"):
        cargar_config(sin_env)


# --- config.toml y orden de prioridad -----------------------------------------------------

from harness.config import (  # noqa: E402
    MENSAJE_SIN_MODELO,
    cargar_env,
    elegir_modelo,
    origen_variable,
    secretos_de_usuario,
)
from harness.config_usuario import ConfigUsuario, Perfil, guardar_config_usuario  # noqa: E402

CLAVE_GEMINI = "clave-gemini-de-config-0123"
CLAVE_GROQ = "clave-groq-de-config-0123"


def _usuario(perfil: str | None = "rapido", **credenciales: str) -> None:
    guardar_config_usuario(ConfigUsuario(
        perfil=perfil,
        perfiles={
            "rapido": Perfil(proveedor="gemini", modelo="flash-lite"),
            "fuerte": Perfil(proveedor="groq", modelo="gpt-oss"),
            "mio": Perfil(proveedor="personalizado", modelo="m", base_url="https://mi.api/v1",
                          api_key_env="MI_KEY"),
        },
        credenciales={"GEMINI_API_KEY": CLAVE_GEMINI, "GROQ_API_KEY": CLAVE_GROQ,
                      **credenciales},
    ))


def test_sin_nada_pide_harness_config(sin_env: Path) -> None:
    with pytest.raises(ErrorConfiguracion, match="ejecuta 'harness config'"):
        cargar_config(sin_env)
    assert "harness config" in MENSAJE_SIN_MODELO


def test_sin_env_usa_el_perfil_predeterminado(sin_env: Path) -> None:
    _usuario()
    config = cargar_config(sin_env)
    assert (config.proveedor, config.modelo, config.api_key) == ("gemini", "flash-lite",
                                                                 CLAVE_GEMINI)
    assert (config.perfil, config.origen) == ("rapido", "predeterminado")


def test_el_env_gana_sobre_el_perfil_predeterminado(sin_env: Path) -> None:
    _usuario()
    sin_env.write_text("HARNESS_PROVEEDOR=groq\nHARNESS_MODELO=otro\n", encoding="utf-8")
    config = cargar_config(sin_env)
    assert (config.proveedor, config.modelo, config.perfil) == ("groq", "otro", None)
    # La key de groq sale de config.toml: el .env no la tiene.
    assert config.api_key == CLAVE_GROQ
    assert config.origen == (
        "HARNESS_PROVEEDOR del .env; gana sobre el perfil 'rapido' de config.toml"
    )


def test_perfil_gana_sobre_el_env(sin_env: Path) -> None:
    _usuario()
    sin_env.write_text("HARNESS_PROVEEDOR=groq\nHARNESS_MODELO=otro\n", encoding="utf-8")
    config = cargar_config(sin_env, perfil="fuerte")
    assert (config.modelo, config.perfil, config.origen) == ("gpt-oss", "fuerte", "--perfil")


def test_proveedor_y_modelo_ganan_sobre_todo(sin_env: Path) -> None:
    _usuario()
    cargar_env(sin_env)
    config = elegir_modelo(cargar_config(sin_env, perfil="rapido"), None, "pro")
    assert (config.proveedor, config.modelo) == ("gemini", "pro")
    config = elegir_modelo(None, "groq", "llama")
    assert (config.proveedor, config.modelo, config.origen) == ("groq", "llama", "--proveedor")
    with pytest.raises(ErrorConfiguracion, match="harness config"):
        elegir_modelo(None, None, "solo-modelo")


def test_perfil_inexistente(sin_env: Path) -> None:
    _usuario()
    with pytest.raises(ErrorConfiguracion, match="No existe el perfil 'nadie'.*rapido, fuerte"):
        cargar_config(sin_env, perfil="nadie")


def test_perfil_personalizado_usa_su_url_y_su_variable(sin_env: Path) -> None:
    _usuario(MI_KEY="clave-propia-0123456789")
    config = cargar_config(sin_env, perfil="mio")
    assert (config.base_url, config.api_key, config.variable_api_key) == (
        "https://mi.api/v1", "clave-propia-0123456789", "MI_KEY"
    )


def test_el_entorno_y_el_env_ganan_sobre_las_credenciales_de_config(
    sin_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _usuario(GITHUB_TOKEN="token-de-config-0123")
    monkeypatch.setenv("GEMINI_API_KEY", "clave-del-entorno-0123")
    sin_env.write_text("GROQ_API_KEY=clave-del-env-0123\n", encoding="utf-8")
    cargar_env(sin_env)
    assert os.environ["GEMINI_API_KEY"] == "clave-del-entorno-0123"
    assert os.environ["GROQ_API_KEY"] == "clave-del-env-0123"
    assert os.environ["GITHUB_TOKEN"] == "token-de-config-0123"
    assert origen_variable("GEMINI_API_KEY") == "el entorno"
    assert origen_variable("GROQ_API_KEY") == "el .env"
    assert origen_variable("GITHUB_TOKEN") == "~/.harness/config.toml"
    assert origen_variable("NO_EXISTE") is None


def test_jira_de_config_entra_al_entorno(sin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from harness.config_usuario import JiraUsuario

    for variable in ("JIRA_URL", "JIRA_EMAIL"):
        monkeypatch.delenv(variable, raising=False)
    guardar_config_usuario(ConfigUsuario(jira=JiraUsuario(url="https://s.atlassian.net",
                                                          email="yo@correo")))
    cargar_env(sin_env)
    assert (os.environ["JIRA_URL"], os.environ["JIRA_EMAIL"]) == ("https://s.atlassian.net",
                                                                  "yo@correo")


def test_todas_las_credenciales_de_config_son_secretos(sin_env: Path) -> None:
    _usuario(OTRA_COSA="valor-que-no-se-usa-0123")
    assert set(secretos_de_usuario()) == {CLAVE_GEMINI, CLAVE_GROQ, "valor-que-no-se-usa-0123"}


def test_config_toml_invalido_es_error_de_configuracion(sin_env: Path) -> None:
    from harness.config_usuario import ruta_config

    ruta_config().parent.mkdir(parents=True)
    ruta_config().write_text("perfil = 'x'\n", encoding="utf-8")
    with pytest.raises(ErrorConfiguracion, match="no existe el perfil 'x'"):
        cargar_env(sin_env)
