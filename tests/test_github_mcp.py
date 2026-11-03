import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from harness.cliente_mcp import HerramientaMCP, SesionMCP
from harness.github import PISTA_PERMISOS, ErrorGitHub, PedidoPullRequest, PullRequest, RepoGitHub
from harness.github_mcp import _codigo_http, crear_pull_request_mcp, puede_crear_pull_request
from tests.datos.servidor_mcp_falso import VAR_REGISTRO_PR
from tests.mcp_prueba import servidor_stdio

REPO = RepoGitHub("gerson", "tareas-api")
URL_PR = "https://github.com/gerson/tareas-api/pull/7"
URL_ENCONTRADO = "https://github.com/gerson/tareas-api/pull/9"


@pytest.fixture(scope="module")
def registro_pr(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("pr") / "llamadas.jsonl"


@pytest.fixture(scope="module")
def sesion(tmp_path_factory: pytest.TempPathFactory, registro_pr: Path) -> Iterator[SesionMCP]:
    ruta_log = tmp_path_factory.mktemp("logs") / "T-1-mcp-github.log"
    servidor = servidor_stdio(env={VAR_REGISTRO_PR: str(registro_pr)})
    with SesionMCP(servidor, ruta_log, "T-1") as abierta:
        yield abierta


@pytest.fixture(autouse=True)
def registro_vacio(registro_pr: Path) -> None:
    """Cada test empieza sin create_pull_request anotados."""
    registro_pr.unlink(missing_ok=True)


def _leer(registro_pr: Path) -> list[dict]:
    if not registro_pr.exists():
        return []
    return [json.loads(l) for l in registro_pr.read_text(encoding="utf-8").splitlines()]


def _pedido(titulo: str = "T-1: Crear tareas", rama: str = "feature/T-1") -> PedidoPullRequest:
    return PedidoPullRequest(REPO, rama, "main", titulo, "## Resumen")


def test_crea_el_pr_en_borrador(sesion: SesionMCP, registro_pr: Path) -> None:
    pr = crear_pull_request_mcp(_pedido(), sesion)
    assert pr == PullRequest(URL_PR, es_borrador=True, por_mcp=True)
    assert _leer(registro_pr) == [{
        "title": "T-1: Crear tareas",
        "head": "feature/T-1",
        "base": "main",
        "body": "## Resumen",
        "draft": True,
    }]


def test_con_html_url_y_draft_en_la_respuesta(sesion: SesionMCP) -> None:
    pr = crear_pull_request_mcp(_pedido("T-1 [html-url]"), sesion)
    assert pr == PullRequest(URL_PR, es_borrador=True, por_mcp=True)


def test_si_el_repo_no_admite_borradores_crea_un_pr_normal(
    sesion: SesionMCP, registro_pr: Path
) -> None:
    pr = crear_pull_request_mcp(_pedido("T-1 [sin-borradores]"), sesion)
    assert pr == PullRequest(URL_PR, es_borrador=False, es_borrador_rechazado=True, por_mcp=True)
    assert [llamada["draft"] for llamada in _leer(registro_pr)] == [True, False]


def test_si_ya_existe_busca_el_pr_de_la_rama(sesion: SesionMCP) -> None:
    pr = crear_pull_request_mcp(_pedido("T-1 [ya-existe]"), sesion)
    assert pr == PullRequest(URL_ENCONTRADO, es_borrador=True, es_existente=True, por_mcp=True)


def test_sin_url_en_la_respuesta_busca_el_pr_recien_creado(
    sesion: SesionMCP
) -> None:
    pr = crear_pull_request_mcp(_pedido("T-1 [sin-url]"), sesion)
    assert pr == PullRequest(URL_ENCONTRADO, es_borrador=True, por_mcp=True)


def test_si_tampoco_lo_encuentra_deja_el_link_de_la_rama(
    sesion: SesionMCP
) -> None:
    pr = crear_pull_request_mcp(_pedido("T-1 [sin-url]", rama="feature/sin-pr"), sesion)
    assert pr.url == (
        "https://github.com/gerson/tareas-api/pulls?q=is%3Apr+head%3Afeature/sin-pr"
    )
    assert pr.es_url_aproximada and pr.por_mcp and pr.es_borrador


def test_un_error_de_github_llega_con_su_motivo_y_su_codigo(
    sesion: SesionMCP
) -> None:
    with pytest.raises(ErrorGitHub) as error:
        crear_pull_request_mcp(_pedido("T-1 [error]"), sesion)
    assert str(error.value) == (
        "GitHub no creó el pull request (por MCP): failed to create pull request: Validation "
        "Failed\nPullRequest.head (invalid): not all refs are readable"
    )
    # Con el 422, entrega.py agrega la pista de permisos, igual que por la API REST.
    assert error.value.codigo == 422
    assert PISTA_PERMISOS not in str(error.value)


# --- Si el servidor sirve para crear el PR ------------------------------------------------

ESQUEMA_COMPLETO = {
    "type": "object",
    "properties": {
        nombre: {"type": "string"}
        for nombre in ("owner", "repo", "title", "head", "base", "body", "draft")
    },
}


def test_puede_crear_pull_request_con_el_servidor_falso(sesion: SesionMCP) -> None:
    assert puede_crear_pull_request(sesion.herramientas)


def test_puede_crear_pull_request_con_el_esquema_real() -> None:
    reales = json.loads(
        (Path(__file__).parent / "datos" / "esquemas_github.json").read_text(encoding="utf-8")
    )
    esquema = reales["create_pull_request"]["input_schema"]
    assert puede_crear_pull_request([HerramientaMCP("create_pull_request", "", esquema)])


@pytest.mark.parametrize(
    "herramientas",
    [
        [],
        [HerramientaMCP("list_commits", "", ESQUEMA_COMPLETO)],
        [HerramientaMCP("create_pull_request", "", {"type": "object", "properties": {}})],
    ],
)
def test_no_puede_crear_pull_request(herramientas: list[HerramientaMCP]) -> None:
    assert not puede_crear_pull_request(herramientas)


# --- Código HTTP de los errores, con los textos reales del servidor ------------------------


@pytest.mark.parametrize(
    ("texto", "codigo"),
    [
        # Respuesta real (base inexistente), capturada del servidor v2.0.3.
        (
            "failed to create pull request: Validation Failed\nPullRequest.base (invalid)\n"
            "PullRequest.head (invalid)",
            422,
        ),
        (
            "failed to create pull request: Validation Failed\n"
            "PullRequest (custom): A pull request already exists for o:feature/T-1.",
            422,
        ),
        ("failed to create pull request: GitHub API validation failed", 422),
        ("failed to create pull request: GET https://api.github.com/x: 404 Not Found", 404),
        ("failed to create pull request: connection reset", None),
    ],
)
def test_codigo_http_de_los_errores_reales(texto: str, codigo: int | None) -> None:
    assert _codigo_http(texto) == codigo
