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
# Archivo donde se anotan los create_pull_request recibidos (uno por línea, en JSON).
VAR_REGISTRO_PR = "FALSO_REGISTRO_PR"
LINEAS_ARCHIVO_LARGO = 400
# El id interno que devuelve GitHub (no es el número del PR).
ID_PR = "3120004567"


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
    def search_pull_requests(query: str) -> str:
        """Busca pull requests; por rama (head:) encuentra el PR, salvo si la rama dice sin-pr."""
        if "head:" not in query or "sin-pr" in query:
            return json.dumps({"incomplete_results": False, "items": [], "total_count": 0})
        repo = query.split("repo:")[1].split()[0]
        item = {"number": 9, "html_url": f"https://github.com/{repo}/pull/9", "draft": True}
        return json.dumps({"incomplete_results": False, "items": [item], "total_count": 1})

    @servidor.tool(structured_output=False)
    def create_pull_request(
        owner: str, repo: str, title: str, head: str, base: str, body: str = "",
        draft: bool = False,
    ) -> CallToolResult | str:
        """Crea un PR; una marca en el título elige cómo responde (como el servidor real)."""
        _anotar({"title": title, "head": head, "base": base, "body": body, "draft": draft})
        if "[sin-borradores]" in title and draft:
            return _error_422("PullRequest (custom): Draft pull requests are not supported in "
                              "this repository.")
        if "[ya-existe]" in title:
            return _error_422(f"PullRequest (custom): A pull request already exists for "
                              f"{owner}:{head}.")
        if "[error]" in title:
            return _error_422("PullRequest.head (invalid): not all refs are readable")
        if "[sin-url]" in title:
            return json.dumps({"id": ID_PR})
        url = f"https://github.com/{owner}/{repo}/pull/7"
        if "[html-url]" in title:
            return json.dumps({"html_url": url, "draft": draft})
        # Lo que responde el servidor real (v2.0.3): el id interno de GitHub y la URL web.
        return json.dumps({"id": ID_PR, "url": url})

    @servidor.tool()
    def pull_request_read(method: str, owner: str, repo: str, pullNumber: int) -> str:
        """Lee un pull request."""
        return json.dumps({"number": pullNumber, "title": "PR anterior"})

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


def _error(texto: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=texto)], is_error=True)


def _error_422(detalle: str) -> CallToolResult:
    # Formato real: el servidor reescribe los 422 sin el código HTTP, con una línea por error.
    return _error(f"failed to create pull request: Validation Failed\n{detalle}")


def _anotar(argumentos: dict) -> None:
    """Anota cada create_pull_request en el archivo de VAR_REGISTRO_PR, si está definida."""
    ruta = os.environ.get(VAR_REGISTRO_PR)
    if ruta:
        with open(ruta, "a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(argumentos, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    crear_servidor().run()
