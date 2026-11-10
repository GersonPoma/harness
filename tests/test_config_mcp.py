import base64
import json
from pathlib import Path

import pytest

from harness.config_mcp import (
    ErrorConfigMCP,
    ServidorHTTP,
    ServidorStdio,
    cargar_config_mcp,
    elegir_servidor_github,
    elegir_servidor_jira,
    motivo_sin_servidor,
)

ENTORNO = {"GITHUB_TOKEN": "github_pat_secreto", "OTRA": "x"}


def _escribir(tmp_path: Path, datos: object) -> Path:
    ruta = tmp_path / "mcp.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


def _cargar(tmp_path: Path, servidores: dict) -> dict:
    return cargar_config_mcp(_escribir(tmp_path, {"mcpServers": servidores}), ENTORNO)


def test_sin_archivo_no_hay_servidores(tmp_path: Path) -> None:
    assert cargar_config_mcp(tmp_path / "mcp.json", ENTORNO) == {}


def test_mcp_json_con_bom(tmp_path: Path) -> None:
    ruta = tmp_path / "mcp.json"
    datos = {"mcpServers": {"github": {"command": "docker", "args": []}}}
    ruta.write_text(json.dumps(datos), encoding="utf-8-sig")
    assert list(cargar_config_mcp(ruta, ENTORNO)) == ["github"]


def test_servidor_stdio_con_el_token_expandido(tmp_path: Path) -> None:
    servidores = _cargar(tmp_path, {
        "github": {
            "command": "docker",
            "args": ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"],
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}",
                "GITHUB_TOOLSETS": "repos,pull_requests",
            },
        }
    })
    servidor = servidores["github"]
    assert isinstance(servidor, ServidorStdio) and servidor.transporte == "stdio"
    assert servidor.comando == "docker"
    assert servidor.args == ("run", "-i", "--rm", "ghcr.io/github/github-mcp-server")
    assert servidor.env == {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "github_pat_secreto",
        "GITHUB_TOOLSETS": "repos,pull_requests",
    }
    # Solo lo que vino de ${...} es secreto; el resto se escribió en el archivo.
    assert servidor.secretos == ("github_pat_secreto",)
    assert "github_pat_secreto" not in repr(servidor)


def test_servidor_http_con_headers(tmp_path: Path) -> None:
    servidores = _cargar(tmp_path, {
        "github-remoto": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {
                "Authorization": "Bearer ${GITHUB_TOKEN}",
                "X-MCP-Toolsets": "repos,pull_requests",
            },
        }
    })
    servidor = servidores["github-remoto"]
    assert isinstance(servidor, ServidorHTTP) and servidor.transporte == "http"
    assert servidor.url == "https://api.githubcopilot.com/mcp/"
    assert servidor.headers["Authorization"] == "Bearer github_pat_secreto"
    assert servidor.secretos == ("github_pat_secreto",)
    assert "github_pat_secreto" not in repr(servidor)


def test_stdio_explicito_con_type(tmp_path: Path) -> None:
    servidores = _cargar(tmp_path, {"s": {"type": "stdio", "command": "npx"}})
    assert servidores["s"] == ServidorStdio("s", "npx", ())


def test_variable_que_no_existe(tmp_path: Path) -> None:
    with pytest.raises(ErrorConfigMCP, match=r"mcpServers\.github\.env\.T: falta .* NO_EXISTE"):
        _cargar(tmp_path, {"github": {"command": "docker", "env": {"T": "${NO_EXISTE}"}}})


@pytest.mark.parametrize(
    ("servidores", "mensaje"),
    [
        ({"github": "docker"}, "debe ser un objeto"),
        ({"github": {"args": ["run"]}}, r"necesita 'command' \(stdio\) o \"type\": \"http\""),
        ({"github": {"type": "sse", "url": "http://x"}}, "necesita 'command'"),
        ({"github": {"type": "http"}}, r"\(http\) necesita 'url'"),
        ({"github": {"command": "docker", "args": "run"}}, "'args' .* lista de textos"),
        ({"github": {"command": "docker", "env": {"N": 1}}}, "'env' .* objeto de textos"),
        ({"github": {"type": "http", "url": "u", "headers": []}}, "'headers' .* objeto"),
    ],
)
def test_servidores_mal_configurados(tmp_path: Path, servidores: dict, mensaje: str) -> None:
    with pytest.raises(ErrorConfigMCP, match=mensaje):
        _cargar(tmp_path, servidores)


@pytest.mark.parametrize("datos", [[], {"servers": {}}, {"mcpServers": []}])
def test_sin_objeto_mcp_servers(tmp_path: Path, datos: object) -> None:
    with pytest.raises(ErrorConfigMCP, match="debe tener un objeto 'mcpServers'"):
        cargar_config_mcp(_escribir(tmp_path, datos), ENTORNO)


def test_json_invalido(tmp_path: Path) -> None:
    ruta = tmp_path / "mcp.json"
    ruta.write_text("{ no es json", encoding="utf-8")
    with pytest.raises(ErrorConfigMCP, match="No se pudo leer mcp.json"):
        cargar_config_mcp(ruta, ENTORNO)


# --- Qué servidor es el de GitHub ----------------------------------------------------------

SERVIDORES = {
    "github": ServidorStdio("github", "docker", ()),
    "github-remoto": ServidorHTTP("github-remoto", "https://api.githubcopilot.com/mcp/"),
}


def test_por_defecto_el_servidor_llamado_github() -> None:
    assert elegir_servidor_github(SERVIDORES, {}) is SERVIDORES["github"]


def test_sin_servidor_github_no_hay_mcp() -> None:
    assert elegir_servidor_github({"otro": SERVIDORES["github"]}, {}) is None


def test_harness_mcp_github_elige_otro_servidor() -> None:
    entorno = {"HARNESS_MCP_GITHUB": "github-remoto"}
    assert elegir_servidor_github(SERVIDORES, entorno) is SERVIDORES["github-remoto"]


def test_harness_mcp_github_con_un_servidor_que_no_existe() -> None:
    with pytest.raises(ErrorConfigMCP, match=r"HARNESS_MCP_GITHUB=remoto, .*hay: github, gith"):
        elegir_servidor_github(SERVIDORES, {"HARNESS_MCP_GITHUB": "remoto"})


# --- Autenticación Basic en servidores HTTP ------------------------------------------------

ENTORNO_JIRA = {"JIRA_EMAIL": "yo@ejemplo.com", "JIRA_MCP_TOKEN": "token-mcp-0123456789"}
CREDENCIAL = base64.b64encode(b"yo@ejemplo.com:token-mcp-0123456789").decode()


def _jira_remoto(**extra: object) -> dict:
    return {"jira-remoto": {
        "type": "http",
        "url": "https://mcp.atlassian.com/v2/mcp",
        "auth": {"basic": {"usuario": "${JIRA_EMAIL}", "token": "${JIRA_MCP_TOKEN}"}},
        **extra,
    }}


def test_auth_basic_arma_el_header(tmp_path: Path) -> None:
    ruta = _escribir(tmp_path, {"mcpServers": _jira_remoto()})
    servidor = cargar_config_mcp(ruta, ENTORNO_JIRA)["jira-remoto"]

    assert servidor.headers == {"Authorization": f"Basic {CREDENCIAL}"}
    # El token y el base64 se ocultan; el email también, porque vino de ${...}.
    assert set(servidor.secretos) == {"yo@ejemplo.com", "token-mcp-0123456789", CREDENCIAL}
    assert servidor.variables_secretas == ("JIRA_EMAIL", "JIRA_MCP_TOKEN")
    assert CREDENCIAL not in repr(servidor)


def test_auth_basic_con_otros_headers(tmp_path: Path) -> None:
    ruta = _escribir(tmp_path, {"mcpServers": _jira_remoto(headers={"X-Otro": "1"})})
    servidor = cargar_config_mcp(ruta, ENTORNO_JIRA)["jira-remoto"]
    assert servidor.headers == {"X-Otro": "1", "Authorization": f"Basic {CREDENCIAL}"}


@pytest.mark.parametrize(
    "auth",
    [
        "Basic abc",
        {"bearer": "x"},
        {"basic": {"usuario": "a"}},
        {"basic": {"usuario": "a", "token": "b", "otro": "c"}},
        {"basic": {"usuario": "a", "token": "b"}, "bearer": "x"},
        {"basic": ["a", "b"]},
    ],
)
def test_auth_con_otra_forma(tmp_path: Path, auth: object) -> None:
    servidores = {"s": {"type": "http", "url": "https://x", "auth": auth}}
    with pytest.raises(ErrorConfigMCP, match=r"'auth' del servidor MCP 's' debe ser"):
        _cargar(tmp_path, servidores)


@pytest.mark.parametrize("nombre_header", ["Authorization", "authorization"])
def test_auth_y_header_authorization_juntos(tmp_path: Path, nombre_header: str) -> None:
    extra = {"headers": {nombre_header: "Bearer otro"}}
    with pytest.raises(ErrorConfigMCP, match="tiene 'auth' y además un header Authorization"):
        cargar_config_mcp(_escribir(tmp_path, {"mcpServers": _jira_remoto(**extra)}), ENTORNO_JIRA)


def test_auth_con_una_variable_que_no_existe(tmp_path: Path) -> None:
    ruta = _escribir(tmp_path, {"mcpServers": _jira_remoto()})
    with pytest.raises(ErrorConfigMCP, match=r"jira-remoto\.auth\.token: falta .* JIRA_MCP_TOKEN"):
        cargar_config_mcp(ruta, {"JIRA_EMAIL": "yo@ejemplo.com"})


# --- Qué servidor es Jira ------------------------------------------------------------------


def test_por_defecto_el_servidor_llamado_jira() -> None:
    servidores = {"jira": ServidorStdio("jira", "docker", ()), **SERVIDORES}
    assert elegir_servidor_jira(servidores, {}) is servidores["jira"]
    assert elegir_servidor_jira(SERVIDORES, {}) is None


def test_harness_mcp_jira_elige_otro_servidor() -> None:
    remoto = ServidorHTTP("jira-remoto", "https://mcp.atlassian.com/v2/mcp")
    servidores = {"jira-remoto": remoto}
    assert elegir_servidor_jira(servidores, {"HARNESS_MCP_JIRA": "jira-remoto"}) is remoto
    with pytest.raises(ErrorConfigMCP, match="HARNESS_MCP_JIRA=jira, pero mcp.json no tiene"):
        elegir_servidor_jira(servidores, {"HARNESS_MCP_JIRA": "jira"})


# --- Forzar la API REST --------------------------------------------------------------------


@pytest.mark.parametrize("valor", ["rest", "REST", " Rest "])
def test_rest_fuerza_la_api_rest_aunque_haya_servidor(valor: str) -> None:
    servidores = {**SERVIDORES, "jira": ServidorStdio("jira", "docker", ())}
    assert elegir_servidor_github(servidores, {"HARNESS_MCP_GITHUB": valor}) is None
    assert elegir_servidor_jira(servidores, {"HARNESS_MCP_JIRA": valor}) is None
    # Cada variable afecta solo a su servicio.
    assert elegir_servidor_jira(servidores, {"HARNESS_MCP_GITHUB": valor}) is servidores["jira"]


def test_motivo_sin_servidor() -> None:
    assert motivo_sin_servidor("HARNESS_MCP_JIRA", "Jira", {"HARNESS_MCP_JIRA": "rest"}) == (
        "HARNESS_MCP_JIRA=rest"
    )
    assert motivo_sin_servidor("HARNESS_MCP_JIRA", "Jira", {}) == (
        "no hay servidor MCP de Jira en mcp.json"
    )


@pytest.mark.parametrize("nombre", ["rest", "Rest"])
def test_rest_es_un_nombre_reservado(tmp_path: Path, nombre: str) -> None:
    with pytest.raises(ErrorConfigMCP, match=f"'{nombre}' es un nombre reservado en mcp.json"):
        _cargar(tmp_path, {nombre: {"command": "docker"}})


# --- ~/.harness/mcp.json y la plantilla ---------------------------------------------------

from harness import config as modulo_config  # noqa: E402
from harness.config_usuario import (  # noqa: E402
    ErrorConfigUsuario,
    crear_mcp_usuario,
    plantilla_mcp,
    ruta_mcp_usuario,
)


def test_usa_el_de_la_carpeta_del_usuario_y_si_no_el_de_desarrollo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    desarrollo = tmp_path / "repo-harness" / "mcp.json"
    monkeypatch.setattr(modulo_config, "RUTA_MCP_DESARROLLO", desarrollo)
    assert modulo_config.ruta_mcp() == desarrollo
    ruta_mcp_usuario().parent.mkdir(parents=True)
    ruta_mcp_usuario().write_text('{"mcpServers": {}}', encoding="utf-8")
    assert modulo_config.ruta_mcp() == ruta_mcp_usuario()


def test_la_plantilla_viaja_en_el_paquete_y_se_puede_leer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    datos = json.loads(plantilla_mcp())
    assert set(datos["mcpServers"]) == {"github", "github-remoto", "jira", "jira-remoto"}
    # Sin secretos: solo referencias ${NOMBRE}.
    assert "${GITHUB_TOKEN}" in plantilla_mcp() and "ghp_" not in plantilla_mcp()
    for variable in ("GITHUB_TOKEN", "JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN",
                     "JIRA_MCP_TOKEN"):
        monkeypatch.setenv(variable, "valor-de-prueba-0123456789")
    ruta = crear_mcp_usuario()
    assert set(cargar_config_mcp(ruta)) == set(datos["mcpServers"])
    with pytest.raises(ErrorConfigUsuario, match="ya existe"):
        crear_mcp_usuario()
