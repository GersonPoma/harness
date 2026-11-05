"""Jira Cloud por su API REST v3: verificar credenciales, leer el ticket y sus comentarios,
y comentar.

El ticket se lee siempre por aquí (no por MCP): el ADF de la v3 pasa por nuestro conversor sin
perder nada. Los servidores MCP probados devuelven la descripción ya convertida y pierden
casillas, paneles o menciones (ver docs/plan-harness-ia.md, "Cómo se conectan los MCP").
"""

from typing import Any

import httpx

from harness.adf import adf_a_markdown
from harness.config import ConfigJira
from harness.ticket import FUENTE_JIRA, Ticket, limitar_descripcion

TIMEOUT_SEGUNDOS = 30
RUTA_API = "/rest/api/3"
RUTA_TENANT_INFO = "/_edge/tenant_info"
# Solo lo que usa el harness: así el autor (email, accountId, avatar) nunca llega.
CAMPOS_TICKET = "summary,description,issuetype,priority,status"
# Los comentarios se leen para no repetir el del PR; con esto alcanza en cualquier ticket.
MAX_COMENTARIOS = 100
CODIGO_NO_AUTORIZADO = 401
CODIGO_PROHIBIDO = 403
CODIGO_NO_ENCONTRADO = 404


class ErrorJira(Exception):
    """Jira rechazó el pedido o no se pudo conectar."""


def obtener_cloud_id(
    config: ConfigJira, transporte: httpx.BaseTransport | None = None
) -> str:
    """Id del sitio que piden las herramientas del servidor MCP remoto de Atlassian.

    /_edge/tenant_info es público: no hace falta ningún token.
    """
    try:
        with httpx.Client(transport=transporte, timeout=TIMEOUT_SEGUNDOS) as http:
            respuesta = http.get(f"{config.url}{RUTA_TENANT_INFO}")
    except httpx.HTTPError as error:
        raise ErrorJira(f"No se pudo conectar con Jira ({config.url}): {error}") from error
    datos = _leer_json(respuesta) if respuesta.is_success else None
    cloud_id = datos.get("cloudId") if isinstance(datos, dict) else None
    if not isinstance(cloud_id, str) or not cloud_id:
        raise ErrorJira(f"No se pudo obtener el cloudId de {config.url}: {_describir(respuesta)}")
    return cloud_id


def url_ticket(config: ConfigJira, clave: str) -> str:
    """Página del ticket en Jira."""
    return f"{config.url}/browse/{clave}"


def verificar_credenciales(
    config: ConfigJira, transporte: httpx.BaseTransport | None = None
) -> None:
    """GET /myself: comprueba el sitio, el email y el token. Lanza ErrorJira con lo que falta."""
    respuesta = _pedir(config, "GET", "/myself", transporte)
    if respuesta.status_code == CODIGO_NO_AUTORIZADO:
        raise ErrorJira(
            "Jira rechazó JIRA_EMAIL o JIRA_API_TOKEN (401): revisa que el token sea de ese "
            "email y que no haya vencido."
        )
    if not respuesta.is_success or _leer_json(respuesta) is None:
        raise ErrorJira(
            f"JIRA_URL ({config.url}) no parece un sitio de Jira Cloud: /rest/api/3/myself "
            f"respondió {_describir(respuesta)}."
        )


def leer_ticket(
    config: ConfigJira, clave: str, transporte: httpx.BaseTransport | None = None
) -> Ticket:
    """Lee el issue y lo normaliza; la descripción ADF pasa a Markdown."""
    respuesta = _pedir(
        config, "GET", f"/issue/{clave}", transporte, params={"fields": CAMPOS_TICKET}
    )
    if respuesta.status_code in (CODIGO_NO_ENCONTRADO, CODIGO_PROHIBIDO):
        raise ErrorJira(
            f"No existe {clave} o tu usuario no tiene permiso para verlo "
            f"(Jira respondió {_describir(respuesta)})."
        )
    datos = _leer_json(respuesta) if respuesta.is_success else None
    if not isinstance(datos, dict):
        raise ErrorJira(f"Jira no devolvió {clave}: {_describir(respuesta)}")
    return _a_ticket(config, clave, datos.get("fields") or {})


def leer_comentarios(
    config: ConfigJira, clave: str, transporte: httpx.BaseTransport | None = None
) -> list[str]:
    """Texto (Markdown) de los comentarios del ticket, para no repetir el link del PR."""
    respuesta = _pedir(
        config, "GET", f"/issue/{clave}/comment", transporte,
        params={"maxResults": MAX_COMENTARIOS},
    )
    datos = _leer_json(respuesta) if respuesta.is_success else None
    if not isinstance(datos, dict):
        raise ErrorJira(f"No se pudieron leer los comentarios de {clave}: {_describir(respuesta)}")
    comentarios = datos.get("comments") or []
    return [adf_a_markdown(c.get("body")) for c in comentarios if isinstance(c, dict)]


def comentar(
    config: ConfigJira,
    clave: str,
    cuerpo_adf: dict[str, Any],
    transporte: httpx.BaseTransport | None = None,
) -> None:
    """Agrega un comentario (en ADF) al ticket."""
    respuesta = _pedir(
        config, "POST", f"/issue/{clave}/comment", transporte, json={"body": cuerpo_adf}
    )
    if not respuesta.is_success:
        raise ErrorJira(f"Jira no agregó el comentario en {clave}: {_describir(respuesta)}")


def texto_comentario_pr(es_borrador: bool) -> str:
    """Comienzo del comentario con el link del PR, según cómo quedó el PR."""
    tipo = "Pull request en borrador" if es_borrador else "Pull request"
    return f"{tipo} creado por harness: "


def comentario_adf(texto: str, url: str) -> dict[str, Any]:
    """El comentario en ADF (lo que pide la API REST v3): el texto y la URL como enlace."""
    enlace = {"type": "text", "text": url, "marks": [{"type": "link", "attrs": {"href": url}}]}
    parrafo = {"type": "paragraph", "content": [{"type": "text", "text": texto}, enlace]}
    return {"type": "doc", "version": 1, "content": [parrafo]}


def _a_ticket(config: ConfigJira, clave: str, campos: dict[str, Any]) -> Ticket:
    descripcion, originales = limitar_descripcion(adf_a_markdown(campos.get("description")))
    return Ticket(
        clave=clave,
        titulo=str(campos.get("summary") or "").strip(),
        descripcion=descripcion,
        fuente=FUENTE_JIRA,
        tipo=_nombre(campos.get("issuetype")),
        prioridad=_nombre(campos.get("priority")),
        estado=_nombre(campos.get("status")),
        url=url_ticket(config, clave),
        caracteres_originales=originales,
    )


def _nombre(campo: Any) -> str | None:
    return str(campo["name"]) if isinstance(campo, dict) and campo.get("name") else None


def _pedir(
    config: ConfigJira,
    metodo: str,
    ruta: str,
    transporte: httpx.BaseTransport | None,
    **opciones: Any,
) -> httpx.Response:
    try:
        with httpx.Client(
            base_url=f"{config.url}{RUTA_API}",
            auth=(config.email, config.token),
            transport=transporte,
            timeout=TIMEOUT_SEGUNDOS,
            headers={"Accept": "application/json"},
        ) as http:
            return http.request(metodo, ruta, **opciones)
    except httpx.HTTPError as error:
        raise ErrorJira(f"No se pudo conectar con Jira ({config.url}): {error}") from error


def _leer_json(respuesta: httpx.Response) -> Any:
    try:
        return respuesta.json()
    except ValueError:
        return None


def _describir(respuesta: httpx.Response) -> str:
    """Código HTTP y los mensajes de error de Jira (errorMessages y errors por campo)."""
    datos = _leer_json(respuesta)
    if not isinstance(datos, dict):
        # Una página HTML (login, sitio que no existe): el código basta.
        return str(respuesta.status_code)
    mensajes = [str(m) for m in datos.get("errorMessages") or []]
    errores = datos.get("errors") or {}
    if isinstance(errores, dict):
        mensajes += [f"{campo}: {mensaje}" for campo, mensaje in errores.items()]
    if not mensajes:
        return str(respuesta.status_code)
    return f"{respuesta.status_code} " + "; ".join(mensajes)
