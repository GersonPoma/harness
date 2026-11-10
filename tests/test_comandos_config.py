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


# --- harness perfiles y harness perfil ------------------------------------------------------

from harness.proveedores.base import ErrorProveedor  # noqa: E402
from harness.tipos import LlamadaHerramienta, RespuestaModelo  # noqa: E402
from tests.falsos import ProveedorFalso  # noqa: E402


def _tres_perfiles(**extra: object) -> None:
    guardar_config_usuario(ConfigUsuario(
        perfil="rapido",
        respaldo=["fuerte", "groq:llama"],
        revisor="fuerte",
        perfiles={
            "rapido": Perfil(proveedor="gemini", modelo="flash"),
            "fuerte": Perfil(proveedor="openrouter", modelo="nemotron:free"),
            "gpt": Perfil(proveedor="openai", modelo="gpt-5"),
        },
        credenciales={"GEMINI_API_KEY": CLAVE, "OPENROUTER_API_KEY": "clave-or-0123456789"},
        **extra,
    ))


def test_perfiles_lista_y_marca_el_predeterminado(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfiles"], terminal=False)
    assert codigo == 0, salida
    assert "* rapido gemini / flash GEMINI_API_KEY ✓" in salida
    assert "fuerte openrouter / nemotron:free OPENROUTER_API_KEY ✓" in salida
    assert "gpt openai / gpt-5 OPENAI_API_KEY ✗ falta" in salida
    assert "Respaldo: fuerte, groq:llama (config.toml)" in salida
    assert "Revisor: fuerte (config.toml)" in salida
    assert "En uso ahora: gemini / flash (perfil rapido, predeterminado)" in salida
    assert CLAVE not in salida


def test_perfiles_sin_perfiles(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida = _invocar(monkeypatch, ["perfiles"], terminal=False)
    assert codigo == 0 and "No hay perfiles" in salida


def test_usar_con_nombre_funciona_sin_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "usar", "fuerte"], terminal=False)
    assert codigo == 0 and "Perfil predeterminado: fuerte (openrouter / nemotron:free)" in salida
    assert leer_config_usuario().perfil == "fuerte"


def test_usar_sin_nombre_y_sin_terminal_pide_el_nombre(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "usar"], terminal=False)
    assert codigo == 1 and "harness perfil usar <nombre>" in salida


def test_usar_sin_nombre_abre_el_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    vistas: list[tuple[list[str], int]] = []

    def elegir_falso(opciones, _consola, inicial=0):
        vistas.append((opciones, inicial))
        return 2

    monkeypatch.setattr(comandos_config.menu, "elegir", elegir_falso)
    codigo, salida = _invocar(monkeypatch, ["perfil", "usar"])
    assert codigo == 0, salida
    opciones, inicial = vistas[0]
    assert inicial == 0 and opciones[0].endswith("[predeterminado]")
    assert leer_config_usuario().perfil == "gpt"


def test_usar_con_menu_cancelado_no_cambia(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    monkeypatch.setattr(comandos_config.menu, "elegir", lambda *_a, **_k: None)
    codigo, salida = _invocar(monkeypatch, ["perfil", "usar"])
    assert codigo == 0 and "Cancelado" in salida and leer_config_usuario().perfil == "rapido"


def test_usar_un_perfil_que_no_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "usar", "nadie"], terminal=False)
    assert codigo == 1 and "No existe el perfil 'nadie' (perfiles: rapido, fuerte, gpt)" in salida


def test_agregar_sin_terminal_con_opciones(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida = _invocar(
        monkeypatch, ["perfil", "agregar", "Mio", "--proveedor", "groq", "--modelo", "llama"],
        terminal=False,
    )
    assert codigo == 0, salida
    usuario = leer_config_usuario()
    # El primero queda como predeterminado; el nombre, en minúsculas.
    assert usuario.perfil == "mio" and usuario.perfiles["mio"].modelo == "llama"
    assert "Falta GROQ_API_KEY: guárdala con 'harness config --credencial GROQ_API_KEY'" in salida


def test_agregar_sin_terminal_y_sin_opciones_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida = _invocar(monkeypatch, ["perfil", "agregar", "mio"], terminal=False)
    assert codigo == 1 and "Falta --proveedor y --modelo" in salida
    assert not ruta_config().exists()


def test_agregar_con_terminal_pregunta_y_pide_la_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "agregar", "local"],
                              "personalizado\nm\nhttps://mi.api/v1\nMI_KEY\n",
                              ["clave-propia-0123456789"])
    assert codigo == 0, salida
    usuario = leer_config_usuario()
    assert usuario.perfiles["local"].base_url == "https://mi.api/v1"
    assert usuario.credenciales["MI_KEY"] == "clave-propia-0123456789"
    assert usuario.perfil == "rapido"


def test_agregar_uno_que_ya_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "agregar", "gpt", "--proveedor", "groq",
                                            "--modelo", "x"], terminal=False)
    assert codigo == 1 and "ya existe" in salida


def test_editar_sin_terminal_aplica_las_opciones(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "editar", "rapido", "--modelo", "pro"],
                              terminal=False)
    assert codigo == 0, salida
    assert leer_config_usuario().perfiles["rapido"] == Perfil(proveedor="gemini", modelo="pro")
    codigo, salida = _invocar(monkeypatch, ["perfil", "editar", "rapido"], terminal=False)
    assert codigo == 1 and "di qué cambiar" in salida


def test_editar_con_terminal_usa_los_valores_actuales(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "editar", "fuerte"], "\notro:free\n")
    assert codigo == 0, salida
    assert leer_config_usuario().perfiles["fuerte"] == Perfil(proveedor="openrouter",
                                                              modelo="otro:free")


def test_borrar_saca_el_perfil_del_respaldo_y_del_revisor(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "borrar", "fuerte"], "s\n")
    assert codigo == 0, salida
    usuario = leer_config_usuario()
    assert "fuerte" not in usuario.perfiles
    assert usuario.respaldo == ["groq:llama"] and usuario.revisor is None
    assert "Se sacó fuerte del respaldo" in salida and "era el revisor" in salida
    # Las credenciales quedan.
    assert "OPENROUTER_API_KEY" in usuario.credenciales


def test_borrar_el_predeterminado_avisa(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "borrar", "rapido", "--si"],
                              terminal=False)
    assert codigo == 0 and leer_config_usuario().perfil is None
    assert "Ya no hay perfil predeterminado" in salida


def test_borrar_sin_terminal_necesita_si(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "borrar", "gpt"], terminal=False)
    assert codigo == 1 and "--si" in salida and "gpt" in leer_config_usuario().perfiles


def test_borrar_y_responder_que_no(monkeypatch: pytest.MonkeyPatch) -> None:
    _tres_perfiles()
    codigo, salida = _invocar(monkeypatch, ["perfil", "borrar", "gpt"], "n\n")
    assert codigo == 0 and "No se borró nada" in salida
    assert "gpt" in leer_config_usuario().perfiles


def _probar(monkeypatch: pytest.MonkeyPatch, respuesta, *argumentos: str) -> tuple[int, str, list]:
    _tres_perfiles()
    pedidos: list = []
    proveedor = ProveedorFalso([respuesta])

    def crear(config, *_):
        pedidos.append(config)
        return proveedor

    monkeypatch.setattr(comandos_config, "crear_proveedor", crear)
    codigo, salida = _invocar(monkeypatch, ["perfil", "probar", *argumentos], terminal=False)
    return codigo, salida, pedidos + [proveedor]


def test_probar_bien(monkeypatch: pytest.MonkeyPatch) -> None:
    llamada = LlamadaHerramienta("p", "responder_prueba", {"texto": "ok"})
    codigo, salida, (config, proveedor) = _probar(
        monkeypatch, RespuestaModelo(None, [llamada], 60, 32)
    )
    assert codigo == 0, salida
    assert config.perfil == "rapido"
    assert "rapido: la key funciona y el modelo llamó a la herramienta" in salida
    assert "92 tokens" in salida
    assert proveedor.obligatorias == ["responder_prueba"]


def test_probar_sin_tool_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida, _ = _probar(monkeypatch, RespuestaModelo("ok", [], 10, 5), "fuerte")
    assert codigo == 1 and "puede no soportar tool calling" in salida


def test_probar_con_la_key_rechazada(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ErrorProveedor("El proveedor 'gemini' rechazó la API key: revisa GEMINI_API_KEY.")
    codigo, salida, _ = _probar(monkeypatch, error)
    assert codigo == 1 and "rapido: El proveedor 'gemini' rechazó la API key" in salida


def test_probar_un_perfil_sin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    codigo, salida, _ = _probar(monkeypatch, RespuestaModelo("ok", [], 1, 1), "gpt")
    assert codigo == 1 and "Falta OPENAI_API_KEY" in salida
