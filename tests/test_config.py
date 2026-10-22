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
    monkeypatch.setenv("GEMINI_API_KEY", "clave")
    config = cargar_config(sin_env)
    assert config.proveedor == "gemini"
    assert config.base_url == PRESETS["gemini"].base_url
    assert config.api_key == "clave"
    assert "clave" not in repr(config)


def test_lee_desde_archivo_env(sin_env: Path) -> None:
    sin_env.write_text(
        "HARNESS_PROVEEDOR=groq\nHARNESS_MODELO=m\nGROQ_API_KEY=k\n", encoding="utf-8"
    )
    assert cargar_config(sin_env).proveedor == "groq"


def test_falta_proveedor(sin_env: Path) -> None:
    with pytest.raises(ErrorConfiguracion, match="Falta HARNESS_PROVEEDOR"):
        cargar_config(sin_env)


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
    monkeypatch.setenv("MI_KEY", "k")
    config = cargar_config(sin_env)
    assert (config.base_url, config.api_key) == ("https://mi.api/v1", "k")


def test_personalizado_sin_base_url(sin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_PROVEEDOR", "personalizado")
    monkeypatch.setenv("HARNESS_MODELO", "m")
    with pytest.raises(ErrorConfiguracion, match="Falta HARNESS_BASE_URL"):
        cargar_config(sin_env)
