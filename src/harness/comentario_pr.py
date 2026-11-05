"""El link del PR en el ticket de Jira: lo comenta el harness (nunca el modelo), sin repetirlo.

Por MCP si hay un servidor de Jira conectado; si la sesión se corta, por la API REST. Si Jira lo
rechaza, solo se avisa: el código ya está validado, subido y el PR existe.
"""

from collections.abc import Callable
from dataclasses import dataclass

from harness import jira
from harness.cliente_mcp import ErrorMCP
from harness.config import ConfigJira
from harness.github import PullRequest
from harness.jira import ErrorJira
from harness.jira_mcp import ComentariosJiraMCP

COMENTADO = "comentado"
YA_ESTABA = "ya_estaba"
FALLIDO = "error"
SIN_URL = "sin_url"
POR_MCP = "mcp"
POR_REST = "rest"


@dataclass(frozen=True)
class ResultadoComentario:
    """Qué pasó con el comentario: comentado, ya estaba, falló, o no se supo la URL del PR."""

    estado: str
    camino: str | None = None
    error: str | None = None


def comentar_pr_en_jira(
    config: ConfigJira,
    clave: str,
    pull_request: PullRequest,
    comentarios_mcp: ComentariosJiraMCP | None,
    al_aviso: Callable[[str], None],
) -> ResultadoComentario:
    """Comenta el link del PR si el ticket todavía no lo tiene."""
    if pull_request.es_url_aproximada:
        # La URL lleva a una búsqueda, no al PR: comentarla confundiría.
        return ResultadoComentario(SIN_URL)
    texto = jira.texto_comentario_pr(pull_request.es_borrador)
    if comentarios_mcp is not None:
        try:
            return _por_mcp(comentarios_mcp, clave, texto, pull_request.url)
        except ErrorMCP as error:
            al_aviso(f"{error} El comentario en {clave} se hace por la API REST de Jira.")
        except ErrorJira as error:
            # Jira lo rechazó (permisos, por ejemplo): por REST pasaría lo mismo.
            return ResultadoComentario(FALLIDO, POR_MCP, str(error))
    try:
        return _por_rest(config, clave, texto, pull_request.url)
    except ErrorJira as error:
        return ResultadoComentario(FALLIDO, POR_REST, str(error))


def _por_mcp(
    comentarios: ComentariosJiraMCP, clave: str, texto: str, url: str
) -> ResultadoComentario:
    if comentarios.tiene_texto(clave, url):
        return ResultadoComentario(YA_ESTABA, POR_MCP)
    comentarios.comentar(clave, jira.comentario_markdown(texto, url))
    return ResultadoComentario(COMENTADO, POR_MCP)


def _por_rest(config: ConfigJira, clave: str, texto: str, url: str) -> ResultadoComentario:
    if any(url in comentario for comentario in jira.leer_comentarios(config, clave)):
        return ResultadoComentario(YA_ESTABA, POR_REST)
    jira.comentar(config, clave, jira.comentario_adf(texto, url))
    return ResultadoComentario(COMENTADO, POR_REST)
