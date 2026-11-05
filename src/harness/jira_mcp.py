"""Comentarios de Jira por MCP: el link del PR, y leer los que hay para no repetirlo.

El ticket no se lee por MCP (ver jira.py). Se reconocen los dos servidores probados por sus
herramientas, porque cada uno las llama distinto:
- el remoto de Atlassian (mcp.atlassian.com): listJiraIssueComments y addOrEditJiraIssueComment,
  que piden el cloudId del sitio;
- sooperset/mcp-atlassian (Docker): jira_get_issue (con sus comentarios) y jira_add_comment.
Los dos aceptan el comentario en Markdown (el enlace, como [texto](url): ver
jira.comentario_markdown).
"""

from collections.abc import Callable

from harness.cliente_mcp import HerramientaMCP, SesionMCP
from harness.config import ConfigJira
from harness.jira import ErrorJira, obtener_cloud_id

REMOTO_LISTAR = "listJiraIssueComments"
REMOTO_COMENTAR = "addOrEditJiraIssueComment"
DOCKER_LEER = "jira_get_issue"
DOCKER_COMENTAR = "jira_add_comment"
# Más que suficiente para encontrar un comentario anterior del harness.
MAX_COMENTARIOS = 100


class ComentariosJiraMCP:
    """Comenta y lee comentarios con el servidor MCP de Jira que esté conectado."""

    def __init__(
        self,
        sesion: SesionMCP,
        leer: Callable[[str], str],
        comentar: Callable[[str, str], None],
    ) -> None:
        self.sesion = sesion
        self._leer = leer
        self._comentar = comentar

    def tiene_texto(self, clave: str, texto: str) -> bool:
        """True si algún comentario del ticket contiene el texto (por ejemplo la URL del PR)."""
        return texto in self._leer(clave)

    def comentar(self, clave: str, markdown: str) -> None:
        """Agrega un comentario en Markdown. Lanza ErrorJira si Jira lo rechaza."""
        self._comentar(clave, markdown)


def preparar_comentarios(
    sesion: SesionMCP, config: ConfigJira
) -> ComentariosJiraMCP | None:
    """Comentarios por el servidor conectado; None si no tiene las herramientas que se usan.

    Lanza ErrorJira si el remoto necesita el cloudId y no se puede obtener.
    """
    nombres = {herramienta.nombre for herramienta in sesion.herramientas}
    if {REMOTO_LISTAR, REMOTO_COMENTAR} <= nombres:
        return _remoto(sesion, obtener_cloud_id(config))
    if {DOCKER_LEER, DOCKER_COMENTAR} <= nombres:
        return _docker(sesion)
    return None


def herramientas_usadas(herramientas: list[HerramientaMCP]) -> list[str]:
    """Las herramientas del servidor que usa el harness (para el log): nunca llegan al modelo."""
    usadas = (REMOTO_LISTAR, REMOTO_COMENTAR, DOCKER_LEER, DOCKER_COMENTAR)
    return [h.nombre for h in herramientas if h.nombre in usadas]


def _remoto(sesion: SesionMCP, cloud_id: str) -> ComentariosJiraMCP:
    def leer(clave: str) -> str:
        return _texto(sesion, REMOTO_LISTAR, {
            "cloudId": cloud_id, "issueIdOrKey": clave, "maxResults": MAX_COMENTARIOS,
        })

    def comentar(clave: str, markdown: str) -> None:
        _texto(sesion, REMOTO_COMENTAR, {
            "cloudId": cloud_id, "issueIdOrKey": clave, "commentBody": markdown,
            "contentFormat": "markdown",
        })

    return ComentariosJiraMCP(sesion, leer, comentar)


def _docker(sesion: SesionMCP) -> ComentariosJiraMCP:
    def leer(clave: str) -> str:
        return _texto(sesion, DOCKER_LEER, {
            "issue_key": clave, "fields": "comment", "comment_limit": MAX_COMENTARIOS,
        })

    def comentar(clave: str, markdown: str) -> None:
        _texto(sesion, DOCKER_COMENTAR, {"issue_key": clave, "body": markdown})

    return ComentariosJiraMCP(sesion, leer, comentar)


def _texto(sesion: SesionMCP, herramienta: str, argumentos: dict) -> str:
    resultado = sesion.llamar(herramienta, argumentos)
    if resultado.es_error:
        raise ErrorJira(f"Jira rechazó {herramienta} (por MCP): {resultado.texto.strip()}")
    return resultado.texto
