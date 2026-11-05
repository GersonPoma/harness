"""Servidor MCP falso de Jira, por stdio: imita al remoto de Atlassian o al de Docker.

FALSO_JIRA_ESTILO=remoto | docker elige los nombres de las herramientas (como los reales).
FALSO_JIRA_COMENTARIOS es un archivo JSON con los comentarios por ticket: se leen y se agregan
ahí, así un test puede ver lo que se comentó o empezar con un comentario ya hecho.
"""

import json
import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

VAR_ESTILO = "FALSO_JIRA_ESTILO"
VAR_COMENTARIOS = "FALSO_JIRA_COMENTARIOS"
# Con este ticket, comentar falla como en Jira (sin permiso para comentar).
TICKET_SIN_PERMISO = "PROHIBIDO-1"


def crear_servidor() -> MCPServer:
    servidor = MCPServer("jira-falso")
    if os.environ.get(VAR_ESTILO) == "docker":
        _herramientas_docker(servidor)
    else:
        _herramientas_remoto(servidor)

    @servidor.tool()
    def jira_search(jql: str) -> str:
        """Busca issues (está para comprobar que ninguna herramienta de Jira llega al modelo)."""
        return json.dumps({"issues": []})

    return servidor


def _herramientas_remoto(servidor: MCPServer) -> None:
    @servidor.tool(name="listJiraIssueComments")
    def listar(cloudId: str, issueIdOrKey: str, maxResults: int = 100) -> str:
        """Comentarios del issue, en Markdown."""
        comentarios = [{"body": cuerpo} for cuerpo in _leer().get(issueIdOrKey, [])]
        return json.dumps({"data": {"total": len(comentarios), "comments": comentarios}})

    @servidor.tool(name="addOrEditJiraIssueComment", structured_output=False)
    def comentar(
        cloudId: str, issueIdOrKey: str, commentBody: str, contentFormat: str = "markdown"
    ) -> CallToolResult | str:
        """Agrega un comentario."""
        return _agregar(issueIdOrKey, commentBody, f"cloudId={cloudId}")


def _herramientas_docker(servidor: MCPServer) -> None:
    @servidor.tool()
    def jira_get_issue(issue_key: str, fields: str = "", comment_limit: int = 10) -> str:
        """El issue, con sus comentarios."""
        comentarios = [{"body": cuerpo} for cuerpo in _leer().get(issue_key, [])]
        return json.dumps({"key": issue_key, "comments": comentarios})

    @servidor.tool(structured_output=False)
    def jira_add_comment(issue_key: str, body: str) -> CallToolResult | str:
        """Agrega un comentario en Markdown."""
        return _agregar(issue_key, body, "")


def _agregar(clave: str, cuerpo: str, extra: str) -> CallToolResult | str:
    if clave == TICKET_SIN_PERMISO:
        texto = "You do not have permission to add comments to this issue."
        return CallToolResult(content=[TextContent(type="text", text=texto)], is_error=True)
    comentarios = _leer()
    comentarios.setdefault(clave, []).append(cuerpo + (f" ({extra})" if extra else ""))
    Path(os.environ[VAR_COMENTARIOS]).write_text(json.dumps(comentarios), encoding="utf-8")
    return json.dumps({"id": str(len(comentarios[clave]))})


def _leer() -> dict[str, list[str]]:
    ruta = Path(os.environ.get(VAR_COMENTARIOS, ""))
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else {}


if __name__ == "__main__":
    crear_servidor().run()
