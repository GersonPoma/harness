"""Servidor MCP falso para los tests: imita al de GitHub con el SDK oficial, sin red.

Por stdio: python servidor_mcp_falso.py (lo lanza el harness como a cualquier servidor).
Por HTTP: crear_servidor().streamable_http_app(), servido con uvicorn desde el fixture.
"""

import json
import os
import time

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

# Lo que devuelve get_file_contents si se le pide 'secreto.txt': el test comprueba que se oculta.
VAR_SECRETO = "FALSO_SECRETO"
LINEAS_ARCHIVO_LARGO = 400


def crear_servidor() -> MCPServer:
    """Servidor con algunas herramientas de lectura, una de escritura y casos de error."""
    servidor = MCPServer("github-falso")

    @servidor.tool()
    def get_file_contents(owner: str, repo: str, path: str = "") -> str:
        """Lee un archivo del repo en GitHub."""
        if path == "secreto.txt":
            return f"token={os.environ.get(VAR_SECRETO, 'sin-secreto')}"
        if path == "largo.txt":
            return "\n".join(f"línea {numero}" for numero in range(LINEAS_ARCHIVO_LARGO))
        return f"contenido de {owner}/{repo}/{path}"

    @servidor.tool()
    def list_commits(owner: str, repo: str) -> str:
        """Lista los commits del repo."""
        return json.dumps([{"sha": "abc123", "commit": {"message": "inicio"}}])

    @servidor.tool()
    def delete_file(owner: str, repo: str, path: str) -> str:
        """Borra un archivo (escritura: nunca debe llegar al modelo)."""
        return "borrado"

    @servidor.tool()
    def falla(motivo: str) -> str:
        """Siempre falla con una excepción (el SDK no reenvía su mensaje)."""
        raise ValueError(f"falló a propósito: {motivo}")

    @servidor.tool(structured_output=False)
    def get_repo(owner: str, repo: str) -> CallToolResult:
        """Falla como el servidor real de GitHub: isError con el motivo en el texto."""
        return CallToolResult(
            content=[TextContent(type="text", text=f"repo no encontrado: {owner}/{repo}")],
            is_error=True,
        )

    @servidor.tool()
    def lento(segundos: float) -> str:
        """Tarda lo pedido en responder."""
        time.sleep(segundos)
        return "listo"

    @servidor.tool()
    def entorno() -> str:
        """Nombres de las variables de entorno del proceso (para probar qué hereda)."""
        return json.dumps(sorted(os.environ))

    return servidor


if __name__ == "__main__":
    crear_servidor().run()
