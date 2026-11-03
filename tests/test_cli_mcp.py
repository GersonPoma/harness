"""harness run con un servidor MCP de GitHub (el falso, por stdio) configurado en mcp.json."""

import json
import sys
from pathlib import Path

import pytest

from harness import cli
from harness.cliente_mcp import ErrorMCP
from harness.prompts import PROMPT_GITHUB_MCP
from harness.tipos import LlamadaHerramienta, Rol
from tests.datos.servidor_mcp_falso import VAR_SECRETO
from tests.falsos import ProveedorFalso, pedir, proponer, terminar
from tests.mcp_prueba import RUTA_SERVIDOR
from tests.git_prueba import requiere_git
from tests.test_cli import (  # noqa: F401 - repo_sin_yaml es un fixture
    _con_github_al_empezar,
    _correr,
    _correr_con_entrega,
    _eventos_del_log,
    _prs_pedidos,
    repo_sin_yaml,
)

NOMBRES_GITHUB = [
    "github__get_file_contents",
    "github__list_commits",
    "github__search_pull_requests",
    "github__pull_request_read",
]


def _con_mcp_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, servidor: dict | None = None
) -> None:
    servidor = servidor or {
        "command": sys.executable,
        "args": [str(RUTA_SERVIDOR)],
        "env": {VAR_SECRETO: "${GITHUB_TOKEN}"},
    }
    ruta = tmp_path / "mcp.json"
    ruta.write_text(json.dumps({"mcpServers": {"github": servidor}}), encoding="utf-8")
    monkeypatch.setattr(cli, "RUTA_MCP", ruta)
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_secreto")


def _sistema(proveedor: ProveedorFalso) -> str:
    (sistema,) = [m for m in proveedor.conversaciones[0] if m.rol == Rol.SISTEMA]
    return sistema.contenido


def test_el_modelo_recibe_las_herramientas_de_github(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_mcp_json(monkeypatch, tmp_path)
    leer = LlamadaHerramienta(
        "c1", "github__get_file_contents", {"owner": "o", "repo": "r", "path": "secreto.txt"}
    )
    proveedor = ProveedorFalso([pedir(leer), terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-plan")

    assert codigo == 0
    assert "GitHub por MCP (servidor github, stdio): 4 herramientas de lectura" in salida
    assert [n for n in proveedor.herramientas[0] if n.startswith("github__")] == NOMBRES_GITHUB
    assert PROMPT_GITHUB_MCP.strip() in _sistema(proveedor)
    eventos = _eventos_del_log(repo_sin_yaml)
    (mcp,) = [e for e in eventos if e["evento"] == "mcp"]
    assert mcp["herramientas"] == NOMBRES_GITHUB and mcp["transporte"] == "stdio"
    # El token llegó al servidor por ${GITHUB_TOKEN} y volvió en la salida: queda oculto.
    (resultado,) = [e for e in eventos if e["evento"] == "herramienta"]
    assert resultado["resultado"] == "token=****"
    assert "github_pat_secreto" not in json.dumps(eventos)
    # Los logs del servidor van a su propio archivo, no a la consola.
    assert (repo_sin_yaml / ".harness" / "logs" / "T-1-mcp-github.log").exists()


@pytest.mark.parametrize("como", ["opcion", "variable"])
def test_sin_herramientas_github_el_modelo_no_las_recibe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path, como: str  # noqa: F811
) -> None:
    _con_mcp_json(monkeypatch, tmp_path)
    opciones = ["--sin-plan"]
    if como == "opcion":
        opciones.append("--sin-herramientas-github")
    else:
        monkeypatch.setenv("HARNESS_MCP_HERRAMIENTAS_MODELO", "0")
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, *opciones)

    assert codigo == 0
    # Sin harness.yaml no hay PR: el servidor no tiene uso y ni se conecta.
    assert "GitHub por MCP: sin herramientas para el modelo y sin PR." in " ".join(salida.split())
    assert not any(n.startswith("github__") for n in proveedor.herramientas[0])
    assert PROMPT_GITHUB_MCP.strip() not in _sistema(proveedor)


def test_sin_mcp_json_no_hay_herramientas_de_github(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-plan")
    assert codigo == 0 and "GitHub por MCP" not in salida
    assert not any(n.startswith("github__") for n in proveedor.herramientas[0])
    assert PROMPT_GITHUB_MCP.strip() not in _sistema(proveedor)


def test_si_el_servidor_no_conecta_avisa_y_sigue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_mcp_json(monkeypatch, tmp_path, {"command": "comando-que-no-existe-xyz"})
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-plan")

    assert codigo == 0
    salida_plana = " ".join(salida.split())
    assert "No se pudo conectar con el servidor MCP 'github'" in salida_plana
    assert "Se sigue sin las herramientas de GitHub para el modelo." in salida_plana
    assert not any(n.startswith("github__") for n in proveedor.herramientas[0])
    (mcp,) = [e for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "mcp"]
    assert mcp["herramientas"] == [] and "No se pudo conectar" in mcp["error"]


def test_un_mcp_json_invalido_detiene_antes_de_empezar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_mcp_json(monkeypatch, tmp_path, {"command": "docker", "env": {"T": "${NO_EXISTE}"}})
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-plan")
    assert codigo == 1
    assert "mcp.json usa variables de entorno que no existen" in " ".join(salida.split())
    assert proveedor.conversaciones == []


def test_en_la_ronda_de_plan_tambien_estan_las_de_github(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_mcp_json(monkeypatch, tmp_path)
    proveedor = ProveedorFalso([proponer(), terminar()])
    codigo, _ = _correr(monkeypatch, repo_sin_yaml, proveedor)

    assert codigo == 0
    plan, implementacion = proveedor.herramientas
    # Son de solo lectura: entran en la ronda de plan junto a leer_archivo y listar_directorio.
    assert set(NOMBRES_GITHUB) <= set(plan) and "escribir_archivo" not in plan
    assert set(NOMBRES_GITHUB) <= set(implementacion)


# --- El PR por MCP, en el preflight y al final ----------------------------------------------

URL_PR_MCP = "https://github.com/gerson/demo/pull/7"
URL_PR_REST = "https://github.com/gerson/demo/pull/3"


def _entrega_con_github(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path, *opciones: str, mcp: bool = True
) -> tuple[int, str, list]:
    """Ejecución completa (falsa) con un remoto que parece de GitHub; devuelve los PR REST."""
    if mcp:
        _con_mcp_json(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_secreto")
    _con_github_al_empezar(monkeypatch)
    pedidos_rest = _prs_pedidos(monkeypatch)
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo, True, *opciones)
    return codigo, " ".join(salida.split()), pedidos_rest


def _entrega_del_log(repo: Path) -> dict:
    (entrega,) = [e for e in _eventos_del_log(repo) if e["evento"] == "entrega"]
    return entrega


@requiere_git
def test_el_pr_se_crea_por_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    codigo, salida, pedidos_rest = _entrega_con_github(monkeypatch, tmp_path, repo_sin_yaml)

    assert codigo == 0
    aviso = (
        "GitHub por MCP (servidor github, stdio): 4 herramientas de lectura para el modelo; "
        "el PR se crea con create_pull_request."
    )
    # En el preflight: antes de crear la rama.
    assert salida.index(aviso) < salida.index("Rama feature/T-1 creada")
    assert f"✓ Pull request en borrador (por MCP): {URL_PR_MCP}" in salida
    assert pedidos_rest == []
    entrega = _entrega_del_log(repo_sin_yaml)
    assert entrega["pull_request"] == URL_PR_MCP and entrega["camino_pr"] == "mcp"


@requiere_git
def test_sin_herramientas_github_el_mcp_queda_solo_para_el_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    codigo, salida, pedidos_rest = _entrega_con_github(
        monkeypatch, tmp_path, repo_sin_yaml, "--sin-herramientas-github"
    )
    assert codigo == 0
    assert (
        "GitHub por MCP (servidor github, stdio): solo para el PR (sin herramientas para el "
        "modelo)." in salida
    )
    assert f"(por MCP): {URL_PR_MCP}" in salida and pedidos_rest == []


@requiere_git
def test_sin_mcp_json_el_pr_va_por_rest_y_se_avisa(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    codigo, salida, pedidos_rest = _entrega_con_github(
        monkeypatch, tmp_path, repo_sin_yaml, mcp=False
    )
    assert codigo == 0
    aviso = "GitHub por la API REST (no hay servidor MCP de GitHub en mcp.json)."
    assert salida.index(aviso) < salida.index("Rama feature/T-1 creada")
    assert f"✓ Pull request en borrador (por la API REST): {URL_PR_REST}" in salida
    assert len(pedidos_rest) == 1
    assert _entrega_del_log(repo_sin_yaml)["camino_pr"] == "rest"


@requiere_git
def test_con_sin_pr_el_mcp_no_se_usa_para_el_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    codigo, salida, pedidos_rest = _entrega_con_github(
        monkeypatch, tmp_path, repo_sin_yaml, "--sin-pr"
    )
    assert codigo == 0
    assert "4 herramientas de lectura para el modelo." in salida
    assert "create_pull_request" not in salida and "Pull request en borrador" not in salida


@requiere_git
def test_si_el_servidor_no_conecta_el_pr_va_por_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_mcp_json(monkeypatch, tmp_path, {"command": "comando-que-no-existe-xyz"})
    codigo, salida, pedidos_rest = _entrega_con_github(
        monkeypatch, tmp_path, repo_sin_yaml, mcp=False
    )
    assert codigo == 0
    assert (
        "Se sigue sin las herramientas de GitHub para el modelo y con el PR por la API REST."
        in salida
    )
    assert f"(por la API REST): {URL_PR_REST}" in salida and len(pedidos_rest) == 1


@requiere_git
def test_si_la_sesion_se_corta_al_final_reintenta_por_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    def se_corta(pedido, sesion):
        raise ErrorMCP("Se cortó la conexión con el servidor MCP 'github'.")

    monkeypatch.setattr(cli, "crear_pull_request_mcp", se_corta)
    codigo, salida, pedidos_rest = _entrega_con_github(monkeypatch, tmp_path, repo_sin_yaml)

    assert codigo == 0
    assert (
        "Se cortó la conexión con el servidor MCP 'github'. Se reintenta el PR por la API REST."
        in salida
    )
    assert f"(por la API REST): {URL_PR_REST}" in salida and len(pedidos_rest) == 1
