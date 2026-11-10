"""harness config: el asistente, --credencial, --importar-env y --mostrar."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli, comandos_config, consola
from harness import config as modulo_config
from harness.config_usuario import (
    ConfigUsuario,
    Perfil,
    guardar_config_usuario,
    leer_config_usuario,
    ruta_config,
    ruta_mcp_usuario,
)

CLAVE = "AIzaSy-clave-de-prueba-x9Q2"
TOKEN = "github_pat_token-de-prueba-a1B3"


@pytest.fixture(autouse=True)
def sin_env_de_desarrollo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """El .env de desarrollo es uno de prueba (vacío al empezar): nunca el de quien corre."""
    ruta = tmp_path / "desarrollo.env"
    monkeypatch.setattr(modulo_config, "RUTA_ENV", ruta)
    for variable in ("HARNESS_PROVEEDOR", "HARNESS_MODELO", "HARNESS_RESPALDO", "GEMINI_API_KEY",
                     "GROQ_API_KEY", "GITHUB_TOKEN", "JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN",
                     "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    return ruta


def _invocar(
    monkeypatch: pytest.MonkeyPatch,
    argumentos: list[str],
    entrada: str = "",
    ocultas: list[str] | None = None,
    terminal: bool = True,
) -> tuple[int, str]:
    monkeypatch.setattr(consola, "es_terminal_interactiva", lambda: terminal)
    valores: Iterator[str] = iter(ocultas or [])
    monkeypatch.setattr(consola, "preguntar_oculto", lambda _texto: next(valores, ""))
    resultado = CliRunner().invoke(cli.app, argumentos, input=entrada)
    return resultado.exit_code, " ".join(resultado.output.split())


def test_el_asistente_guarda_perfil_y_credenciales(monkeypatch: pytest.MonkeyPatch) -> None:
    # Proveedor, modelo, nombre del perfil, GitHub sí, Jira no, mcp.json no.
    entrada = "gemini\nflash-lite\nrapido\ns\nn\nn\n"
    codigo, salida = _invocar(monkeypatch, ["config"], entrada, [CLAVE, TOKEN])
    assert codigo == 0, salida
    usuario = leer_config_usuario()
    assert usuario.perfil == "rapido"
    assert usuario.perfiles["rapido"] == Perfil(proveedor="gemini", modelo="flash-lite")
    assert usuario.credenciales == {"GEMINI_API_KEY": CLAVE, "GITHUB_TOKEN": TOKEN}
    assert "Perfil predeterminado: rapido (gemini / flash-lite)" in salida
    assert "harness perfil probar rapido" in salida
    # Nunca el secreto completo en pantalla.
    assert CLAVE not in salida and TOKEN not in salida
    assert not ruta_mcp_usuario().exists()


def test_el_asistente_con_jira_y_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    entrada = "groq\ngpt-oss\n\nn\ns\nhttps://s.atlassian.net\nyo@correo\ns\n"
    codigo, salida = _invocar(monkeypatch, ["config"], entrada, ["clave-groq-0123456789",
                                                                 "token-jira-0123456789"])
    assert codigo == 0, salida
    usuario = leer_config_usuario()
    # Enter en el nombre: el del proveedor.
    assert usuario.perfil == "groq"
    assert (usuario.jira.url, usuario.jira.email) == ("https://s.atlassian.net", "yo@correo")
    assert usuario.credenciales["JIRA_API_TOKEN"] == "token-jira-0123456789"
    assert ruta_mcp_usuario().is_file() and "Creado" in salida


def test_con_config_existente_enter_deja_la_key(monkeypatch: pytest.MonkeyPatch) -> None:
    guardar_config_usuario(ConfigUsuario(
        perfil="rapido", perfiles={"rapido": Perfil(proveedor="gemini", modelo="flash")},
        credenciales={"GEMINI_API_KEY": CLAVE},
    ))
    pistas: list[str] = []
    monkeypatch.setattr(consola, "es_terminal_interactiva", lambda: True)
    monkeypatch.setattr(consola, "preguntar_oculto", lambda texto: pistas.append(texto) or "")
    # Enter en todo: proveedor, modelo y nombre quedan como estaban.
    resultado = CliRunner().invoke(cli.app, ["config"], input="\n\n\nn\nn\nn\n")
    assert resultado.exit_code == 0, resultado.output
    assert leer_config_usuario().credenciales == {"GEMINI_API_KEY": CLAVE}
    assert pistas == ["GEMINI_API_KEY (guardada: ****x9Q2, Enter para dejarla)"]


def test_sin_terminal_el_asistente_no_pregunta(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida = _invocar(monkeypatch, ["config"], terminal=False)
    assert codigo == 1
    assert "--importar-env" in salida and "--credencial NOMBRE" in salida
    assert not ruta_config().exists()


def test_credencial_por_stdin_sin_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida = _invocar(
        monkeypatch, ["config", "--credencial", "GITHUB_TOKEN"], f"{TOKEN}\n", terminal=False
    )
    assert codigo == 0, salida
    assert leer_config_usuario().credenciales == {"GITHUB_TOKEN": TOKEN}
    assert "GITHUB_TOKEN guardada" in salida and "****a1B3" in salida and TOKEN not in salida


def test_credencial_oculta_con_terminal_y_aviso_si_el_env_gana(
    monkeypatch: pytest.MonkeyPatch, sin_env_de_desarrollo: Path
) -> None:
    sin_env_de_desarrollo.write_text("GITHUB_TOKEN=otro-token-0123456789\n", encoding="utf-8")
    codigo, salida = _invocar(monkeypatch, ["config", "--credencial", "GITHUB_TOKEN"],
                              ocultas=[TOKEN])
    assert codigo == 0, salida
    assert "también está en el .env, que gana sobre config.toml" in salida


@pytest.mark.parametrize(
    ("nombre", "mensaje"),
    [("MAL NOMBRE", "no es un nombre de variable"), ("HARNESS_MODELO", "es configuración")],
)
def test_credencial_invalida(monkeypatch: pytest.MonkeyPatch, nombre: str, mensaje: str) -> None:
    codigo, salida = _invocar(monkeypatch, ["config", "--credencial", nombre], ocultas=["x"])
    assert codigo == 1 and mensaje in salida


def test_credencial_vacia_no_guarda(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida = _invocar(monkeypatch, ["config", "--credencial", "GITHUB_TOKEN"],
                              terminal=False)
    assert codigo == 1 and "No se recibió ningún valor" in salida
    assert not ruta_config().exists()


def test_importar_env(monkeypatch: pytest.MonkeyPatch, sin_env_de_desarrollo: Path) -> None:
    sin_env_de_desarrollo.write_text(
        "HARNESS_PROVEEDOR=gemini\nHARNESS_MODELO=flash-lite\n"
        f"GEMINI_API_KEY={CLAVE}\nGITHUB_TOKEN={TOKEN}\n"
        "HARNESS_RESPALDO=openrouter:nemotron:free\nJIRA_URL=https://s.atlassian.net\n"
        "OTRA_COSA=no-se-copia\n",
        encoding="utf-8",
    )
    codigo, salida = _invocar(monkeypatch, ["config", "--importar-env", "--perfil", "dev"],
                              terminal=False)
    assert codigo == 0, salida
    usuario = leer_config_usuario()
    assert usuario.perfil == "dev"
    assert usuario.perfiles["dev"] == Perfil(proveedor="gemini", modelo="flash-lite")
    assert usuario.credenciales == {"GEMINI_API_KEY": CLAVE, "GITHUB_TOKEN": TOKEN}
    assert usuario.respaldo == ["openrouter:nemotron:free"]
    assert usuario.jira.url == "https://s.atlassian.net"
    assert "perfil dev: gemini / flash-lite" in salida and "****x9Q2" in salida
    assert CLAVE not in salida
    # El .env sigue teniendo HARNESS_PROVEEDOR: se avisa que gana sobre el perfil.
    assert "gana sobre el perfil predeterminado" in salida


def test_importar_env_sin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida = _invocar(monkeypatch, ["config", "--importar-env"], terminal=False)
    assert codigo == 1 and "No hay .env de desarrollo" in salida


def test_mostrar_enmascara_y_dice_el_origen(
    monkeypatch: pytest.MonkeyPatch, sin_env_de_desarrollo: Path
) -> None:
    guardar_config_usuario(ConfigUsuario(
        perfil="rapido", perfiles={"rapido": Perfil(proveedor="gemini", modelo="flash"),
                                   "gpt": Perfil(proveedor="openai", modelo="gpt-5")},
        credenciales={"GEMINI_API_KEY": CLAVE, "GITHUB_TOKEN": TOKEN},
    ))
    sin_env_de_desarrollo.write_text("GEMINI_API_KEY=otra-clave-del-env-7777\n",
                                     encoding="utf-8")
    codigo, salida = _invocar(monkeypatch, ["config", "--mostrar"], terminal=False)
    assert codigo == 0, salida
    assert CLAVE not in salida and TOKEN not in salida
    assert "GEMINI_API_KEY ****7777 (el .env; config.toml también tiene una)" in salida
    assert "GITHUB_TOKEN ****a1B3 (~/.harness/config.toml)" in salida
    assert "JIRA_API_TOKEN no está" in salida
    assert "* rapido gemini / flash GEMINI_API_KEY ✓" in salida
    assert "gpt openai / gpt-5 OPENAI_API_KEY ✗ falta" in salida
    assert "En uso ahora: gemini / flash (perfil rapido, predeterminado)" in salida


def test_un_config_toml_roto_se_informa(monkeypatch: pytest.MonkeyPatch) -> None:
    ruta_config().parent.mkdir(parents=True)
    ruta_config().write_text("perfil = 'nadie'\n", encoding="utf-8")
    codigo, salida = _invocar(monkeypatch, ["config", "--mostrar"], terminal=False)
    assert codigo == 1 and "no existe el perfil 'nadie'" in salida


def test_el_entorno_no_cambia_al_terminar() -> None:
    # El conftest restaura os.environ: las credenciales cargadas por un test no pasan al otro.
    assert "GITHUB_TOKEN" not in os.environ or os.environ["GITHUB_TOKEN"] != TOKEN
