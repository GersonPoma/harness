import pytest

from harness import comentario_pr
from harness.cliente_mcp import ErrorMCP
from harness.comentario_pr import (
    COMENTADO,
    FALLIDO,
    POR_MCP,
    POR_REST,
    SIN_URL,
    YA_ESTABA,
    ResultadoComentario,
    comentar_pr_en_jira,
)
from harness.config import ConfigJira
from harness.github import PullRequest
from harness.jira import ErrorJira, comentario_adf, texto_comentario_pr

CONFIG = ConfigJira("https://ejemplo.atlassian.net", "yo@ejemplo.com", "jira-token-falso-123")
URL_PR = "https://github.com/o/r/pull/7"
BORRADOR = PullRequest(URL_PR, es_borrador=True)


class JiraRestFalso:
    """Reemplaza leer_comentarios y comentar de jira.py, y anota lo que se comentó."""

    def __init__(self, comentarios: list[str] | None = None, error: str | None = None) -> None:
        self.comentarios = comentarios or []
        self.enviados: list[dict] = []
        self._error = error

    def leer(self, config: ConfigJira, clave: str) -> list[str]:
        return self.comentarios

    def comentar(self, config: ConfigJira, clave: str, cuerpo: dict) -> None:
        if self._error:
            raise ErrorJira(self._error)
        self.enviados.append(cuerpo)


class ComentariosMCPFalso:
    def __init__(self, existentes: str = "", error: Exception | None = None) -> None:
        self.existentes = existentes
        self.enviados: list[str] = []
        self._error = error

    def tiene_texto(self, clave: str, texto: str) -> bool:
        if self._error:
            raise self._error
        return texto in self.existentes

    def comentar(self, clave: str, markdown: str) -> None:
        self.enviados.append(markdown)


@pytest.fixture
def rest(monkeypatch: pytest.MonkeyPatch) -> JiraRestFalso:
    falso = JiraRestFalso()
    monkeypatch.setattr(comentario_pr.jira, "leer_comentarios", falso.leer)
    monkeypatch.setattr(comentario_pr.jira, "comentar", falso.comentar)
    return falso


def _comentar(pr: PullRequest, mcp=None, avisos: list | None = None) -> ResultadoComentario:
    al_aviso = (avisos if avisos is not None else []).append
    return comentar_pr_en_jira(CONFIG, "T-1", pr, mcp, al_aviso)


def test_por_rest_comenta_en_adf(rest: JiraRestFalso) -> None:
    assert _comentar(BORRADOR) == ResultadoComentario(COMENTADO, POR_REST)
    assert rest.enviados == [
        comentario_adf("Pull request en borrador creado por harness: ", URL_PR)
    ]


def test_si_el_borrador_no_se_admitio_el_texto_no_dice_borrador(rest: JiraRestFalso) -> None:
    normal = PullRequest(URL_PR, es_borrador=False, es_borrador_rechazado=True)
    _comentar(normal)
    (enviado,) = rest.enviados
    assert enviado["content"][0]["content"][0]["text"] == "Pull request creado por harness: "


def test_por_rest_no_repite_el_comentario(rest: JiraRestFalso) -> None:
    rest.comentarios = ["Otro comentario", f"Pull request en borrador: [{URL_PR}]({URL_PR})"]
    assert _comentar(BORRADOR) == ResultadoComentario(YA_ESTABA, POR_REST)
    assert rest.enviados == []


def test_por_rest_con_error(monkeypatch: pytest.MonkeyPatch) -> None:
    falso = JiraRestFalso(error="Jira no agregó el comentario en T-1: 403")
    monkeypatch.setattr(comentario_pr.jira, "leer_comentarios", falso.leer)
    monkeypatch.setattr(comentario_pr.jira, "comentar", falso.comentar)
    assert _comentar(BORRADOR) == ResultadoComentario(
        FALLIDO, POR_REST, "Jira no agregó el comentario en T-1: 403"
    )


def test_por_mcp_comenta_en_markdown(rest: JiraRestFalso) -> None:
    mcp = ComentariosMCPFalso()
    assert _comentar(BORRADOR, mcp) == ResultadoComentario(COMENTADO, POR_MCP)
    assert mcp.enviados == [f"Pull request en borrador creado por harness: {URL_PR}"]
    assert rest.enviados == []


def test_por_mcp_no_repite_el_comentario(rest: JiraRestFalso) -> None:
    mcp = ComentariosMCPFalso(existentes=f'{{"comments": [{{"body": "PR: {URL_PR}"}}]}}')
    assert _comentar(BORRADOR, mcp) == ResultadoComentario(YA_ESTABA, POR_MCP)
    assert mcp.enviados == []


def test_si_la_sesion_mcp_se_corta_comenta_por_rest(rest: JiraRestFalso) -> None:
    avisos: list[str] = []
    mcp = ComentariosMCPFalso(error=ErrorMCP("Se cortó la conexión con el servidor MCP 'jira'."))
    assert _comentar(BORRADOR, mcp, avisos) == ResultadoComentario(COMENTADO, POR_REST)
    assert avisos == [
        "Se cortó la conexión con el servidor MCP 'jira'. El comentario en T-1 se hace por la "
        "API REST de Jira."
    ]
    assert len(rest.enviados) == 1


def test_si_jira_rechaza_por_mcp_no_reintenta_por_rest(rest: JiraRestFalso) -> None:
    rechazo = ErrorJira("Jira rechazó jira_add_comment (por MCP): sin permiso")
    mcp = ComentariosMCPFalso(error=rechazo)
    resultado = _comentar(BORRADOR, mcp)
    assert resultado.estado == FALLIDO and resultado.camino == POR_MCP
    assert rest.enviados == []


def test_sin_la_url_exacta_del_pr_no_comenta(rest: JiraRestFalso) -> None:
    aproximada = PullRequest(
        "https://github.com/o/r/pulls?q=head%3Afeature/T-1", es_borrador=True,
        por_mcp=True, es_url_aproximada=True,
    )
    mcp = ComentariosMCPFalso()
    assert _comentar(aproximada, mcp) == ResultadoComentario(SIN_URL)
    assert mcp.enviados == [] and rest.enviados == []


def test_texto_y_adf_del_comentario() -> None:
    assert texto_comentario_pr(True) == "Pull request en borrador creado por harness: "
    assert texto_comentario_pr(False) == "Pull request creado por harness: "
    assert comentario_adf("PR: ", URL_PR) == {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [
            {"type": "text", "text": "PR: "},
            {"type": "text", "text": URL_PR,
             "marks": [{"type": "link", "attrs": {"href": URL_PR}}]},
        ]},
    ]}
