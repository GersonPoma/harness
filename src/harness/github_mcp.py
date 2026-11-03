"""Pull request con create_pull_request del servidor MCP de GitHub, ejecutado por el harness.

Devuelve los mismos PullRequest y ErrorGitHub que el cliente REST (github.py), así entrega.py
no sabe por qué camino se creó.
"""

import json
import re
from typing import Any

from harness.cliente_mcp import HerramientaMCP, SesionMCP
from harness.github import (
    TEXTO_SIN_BORRADORES,
    TEXTO_YA_EXISTE,
    URL_WEB,
    ErrorGitHub,
    PedidoPullRequest,
    PullRequest,
)

HERRAMIENTA_CREAR_PR = "create_pull_request"
HERRAMIENTA_BUSCAR_PR = "search_pull_requests"
# Los parámetros que el harness le manda; sin alguno de ellos, el PR va por la API REST.
PARAMETROS_CREAR_PR = ("owner", "repo", "title", "head", "base", "body", "draft")
# Algunos errores del servidor traen el código HTTP ("...: 404 Not Found").
PATRON_CODIGO_HTTP = re.compile(r"\b([45]\d\d)\b")
# Los 422 no: el servidor los reescribe como "failed to create pull request: Validation
# Failed" seguido de una línea por error ("PullRequest (custom): A pull request already...").
# Solo arma ese texto para respuestas 422, así que alcanza para reconocerlos.
TEXTOS_VALIDACION = ("validation failed", "github api validation failed")
CODIGO_VALIDACION = 422


def puede_crear_pull_request(herramientas: list[HerramientaMCP]) -> bool:
    """True si el servidor tiene create_pull_request con todos los parámetros que se usan."""
    for herramienta in herramientas:
        if herramienta.nombre == HERRAMIENTA_CREAR_PR:
            propiedades = herramienta.esquema.get("properties", {})
            return all(parametro in propiedades for parametro in PARAMETROS_CREAR_PR)
    return False


def crear_pull_request_mcp(pedido: PedidoPullRequest, sesion: SesionMCP) -> PullRequest:
    """Abre el PR en borrador; si el repo no admite borradores, lo abre normal.

    Lanza ErrorGitHub si GitHub lo rechaza, y ErrorMCP si se cortó la sesión.
    """
    resultado = sesion.llamar(HERRAMIENTA_CREAR_PR, _argumentos(pedido, es_borrador=True))
    es_rechazado = resultado.es_error and TEXTO_SIN_BORRADORES in resultado.texto.lower()
    if es_rechazado:
        resultado = sesion.llamar(HERRAMIENTA_CREAR_PR, _argumentos(pedido, es_borrador=False))
    if resultado.es_error and TEXTO_YA_EXISTE in resultado.texto.lower():
        return _buscar_o_link(pedido, sesion, es_existente=True)
    if resultado.es_error:
        raise ErrorGitHub(
            f"GitHub no creó el pull request (por MCP): {resultado.texto.strip()}",
            _codigo_http(resultado.texto),
        )
    datos = _leer_json(resultado.texto)
    url = datos.get("html_url") or datos.get("url")
    if isinstance(url, str) and url.startswith(f"{URL_WEB}/"):
        es_borrador = bool(datos.get("draft", not es_rechazado))
        return PullRequest(url, es_borrador, por_mcp=True, es_borrador_rechazado=es_rechazado)
    # La respuesta no dice dónde quedó: se busca el PR recién creado por su rama.
    return _buscar_o_link(pedido, sesion, es_borrador_rechazado=es_rechazado)


def url_prs_de_la_rama(pedido: PedidoPullRequest) -> str:
    """Lista de PRs de la rama en GitHub: el último recurso si no se sabe la URL del PR."""
    repo = pedido.repo
    return f"{URL_WEB}/{repo.duenio}/{repo.nombre}/pulls?q=is%3Apr+head%3A{pedido.rama}"


def _argumentos(pedido: PedidoPullRequest, es_borrador: bool) -> dict[str, Any]:
    return {
        "owner": pedido.repo.duenio,
        "repo": pedido.repo.nombre,
        "title": pedido.titulo,
        "head": pedido.rama,
        "base": pedido.base,
        "body": pedido.cuerpo,
        "draft": es_borrador,
    }


def _buscar_o_link(
    pedido: PedidoPullRequest,
    sesion: SesionMCP,
    es_existente: bool = False,
    es_borrador_rechazado: bool = False,
) -> PullRequest:
    repo = pedido.repo
    consulta = f"repo:{repo.duenio}/{repo.nombre} is:pr is:open head:{pedido.rama}"
    resultado = sesion.llamar(HERRAMIENTA_BUSCAR_PR, {"query": consulta})
    items = [] if resultado.es_error else _leer_json(resultado.texto).get("items", [])
    if items and isinstance(items[0], dict) and items[0].get("html_url"):
        return PullRequest(
            items[0]["html_url"],
            bool(items[0].get("draft")),
            es_existente=es_existente,
            es_borrador_rechazado=es_borrador_rechazado,
            por_mcp=True,
        )
    return PullRequest(
        url_prs_de_la_rama(pedido),
        es_borrador=not es_borrador_rechazado,
        es_existente=es_existente,
        es_borrador_rechazado=es_borrador_rechazado,
        por_mcp=True,
        es_url_aproximada=True,
    )


def _leer_json(texto: str) -> dict[str, Any]:
    try:
        datos = json.loads(texto)
    except ValueError:
        return {}
    return datos if isinstance(datos, dict) else {}


def _codigo_http(texto: str) -> int | None:
    coincidencia = PATRON_CODIGO_HTTP.search(texto)
    if coincidencia:
        return int(coincidencia[1])
    if any(validacion in texto.lower() for validacion in TEXTOS_VALIDACION):
        return CODIGO_VALIDACION
    return None
