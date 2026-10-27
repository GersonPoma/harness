"""Pull requests en GitHub, con su API REST.

Necesita un token fine-grained con "Pull requests: Read and write" sobre el repo (en GITHUB_TOKEN
del .env). El push no lo usa: va con las credenciales de Git del usuario.
"""

import re
from dataclasses import dataclass

import httpx

URL_API = "https://api.github.com"
VERSION_API = "2022-11-28"
TIMEOUT_SEGUNDOS = 30
CODIGO_CREADO = 201
CODIGO_NO_PROCESABLE = 422
# github.com/<dueño>/<repo>, en https (con o sin usuario) o ssh, con o sin ".git" al final.
PATRON_REMOTO = re.compile(
    r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<duenio>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
# Lo que responde GitHub (422) en un repo privado de una cuenta gratuita.
TEXTO_SIN_BORRADORES = "draft pull requests are not supported"
TEXTO_YA_EXISTE = "a pull request already exists"


class ErrorGitHub(Exception):
    """La API de GitHub rechazó el pedido."""


@dataclass(frozen=True)
class RepoGitHub:
    """Dueño y nombre de un repo de GitHub."""

    duenio: str
    nombre: str


@dataclass(frozen=True)
class PullRequest:
    """PR creado (o que ya existía) y cómo quedó."""

    url: str
    es_borrador: bool
    es_existente: bool = False
    # True si se pidió borrador y GitHub no lo permite en este repo.
    es_borrador_rechazado: bool = False


@dataclass(frozen=True)
class PedidoPullRequest:
    """Lo que hace falta para abrir el PR."""

    repo: RepoGitHub
    rama: str
    base: str
    titulo: str
    cuerpo: str


def repo_desde_remoto(url: str) -> RepoGitHub | None:
    """Dueño y repo a partir de la URL del remoto; None si no es de GitHub."""
    coincidencia = PATRON_REMOTO.match(url.strip())
    if coincidencia is None:
        return None
    return RepoGitHub(coincidencia["duenio"], coincidencia["repo"])


def crear_pull_request(
    pedido: PedidoPullRequest, token: str, transporte: httpx.BaseTransport | None = None
) -> PullRequest:
    """Abre el PR en borrador; si el repo no admite borradores, lo abre normal.

    `transporte` reemplaza la red en los tests (httpx.MockTransport).
    """
    try:
        with _crear_cliente(token, transporte) as http:
            respuesta = _pedir_pr(http, pedido, es_borrador=True)
            if _es_error(respuesta, TEXTO_SIN_BORRADORES):
                respuesta = _pedir_pr(http, pedido, es_borrador=False)
                return _resultado(http, pedido, respuesta, es_borrador_rechazado=True)
            return _resultado(http, pedido, respuesta, es_borrador_rechazado=False)
    except httpx.HTTPError as error:
        raise ErrorGitHub(f"No se pudo conectar con GitHub: {error}") from error


def _crear_cliente(token: str, transporte: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url=URL_API,
        transport=transporte,
        timeout=TIMEOUT_SEGUNDOS,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": VERSION_API,
        },
    )


def _pedir_pr(http: httpx.Client, pedido: PedidoPullRequest, es_borrador: bool) -> httpx.Response:
    return http.post(
        f"/repos/{pedido.repo.duenio}/{pedido.repo.nombre}/pulls",
        json={
            "title": pedido.titulo,
            "head": pedido.rama,
            "base": pedido.base,
            "body": pedido.cuerpo,
            "draft": es_borrador,
        },
    )


def _resultado(
    http: httpx.Client,
    pedido: PedidoPullRequest,
    respuesta: httpx.Response,
    es_borrador_rechazado: bool,
) -> PullRequest:
    if respuesta.status_code == CODIGO_CREADO:
        datos = respuesta.json()
        return PullRequest(
            datos["html_url"],
            bool(datos.get("draft")),
            es_borrador_rechazado=es_borrador_rechazado,
        )
    if _es_error(respuesta, TEXTO_YA_EXISTE):
        return _pr_existente(http, pedido)
    raise ErrorGitHub(f"GitHub no creó el pull request: {_describir(respuesta)}")


def _pr_existente(http: httpx.Client, pedido: PedidoPullRequest) -> PullRequest:
    respuesta = http.get(
        f"/repos/{pedido.repo.duenio}/{pedido.repo.nombre}/pulls",
        params={"head": f"{pedido.repo.duenio}:{pedido.rama}", "state": "open"},
    )
    abiertos = respuesta.json() if respuesta.is_success else []
    if not isinstance(abiertos, list) or not abiertos:
        raise ErrorGitHub(f"GitHub dice que el pull request ya existe: {_describir(respuesta)}")
    return PullRequest(abiertos[0]["html_url"], bool(abiertos[0].get("draft")), es_existente=True)


def _es_error(respuesta: httpx.Response, texto: str) -> bool:
    return respuesta.status_code == CODIGO_NO_PROCESABLE and texto in _describir(respuesta).lower()


def _describir(respuesta: httpx.Response) -> str:
    """Código HTTP y mensajes de error de GitHub (los de 'errors' dicen la causa exacta)."""
    try:
        datos = respuesta.json()
    except ValueError:
        return f"{respuesta.status_code} {respuesta.text[:200]}"
    mensajes = [str(datos.get("message", ""))]
    for error in datos.get("errors", []) if isinstance(datos, dict) else []:
        mensajes.append(str(error.get("message") or error.get("code", "")))
    return f"{respuesta.status_code} " + "; ".join(m for m in mensajes if m)
