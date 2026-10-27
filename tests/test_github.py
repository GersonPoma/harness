import json

import httpx
import pytest

from harness.github import (
    ErrorGitHub,
    PedidoPullRequest,
    PullRequest,
    RepoGitHub,
    crear_pull_request,
    repo_desde_remoto,
)

REPO = RepoGitHub("gerson", "tareas-api")
PEDIDO = PedidoPullRequest(REPO, "feature/T-1", "main", "T-1: Crear tareas", "## Resumen")
TOKEN = "github_pat_prueba"
URL_PR = "https://github.com/gerson/tareas-api/pull/7"
RUTA_PULLS = "/repos/gerson/tareas-api/pulls"


def _error_422(mensaje: str) -> httpx.Response:
    cuerpo = {"message": "Validation Failed", "errors": [{"code": "custom", "message": mensaje}]}
    return httpx.Response(422, json=cuerpo)


class GitHubFalso:
    """Responde en orden y guarda los pedidos que recibió."""

    def __init__(self, *respuestas: httpx.Response) -> None:
        self._respuestas = list(respuestas)
        self.pedidos: list[httpx.Request] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        return self._respuestas.pop(0)

    @property
    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def cuerpo(self, indice: int) -> dict:
        return json.loads(self.pedidos[indice].content)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/gerson/tareas-api.git",
        "https://github.com/gerson/tareas-api",
        "https://gerson@github.com/gerson/tareas-api.git",
        "https://github.com/gerson/tareas-api/",
        "git@github.com:gerson/tareas-api.git",
        "ssh://git@github.com/gerson/tareas-api.git",
    ],
)
def test_repo_desde_remoto_de_github(url: str) -> None:
    assert repo_desde_remoto(url) == REPO


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/gerson/tareas-api.git",
        "C:/repos/remoto.git",
        "/tmp/remoto.git",
        "https://github.com/solo-duenio",
    ],
)
def test_remotos_que_no_son_de_github(url: str) -> None:
    assert repo_desde_remoto(url) is None


def test_crea_el_pr_en_borrador() -> None:
    github = GitHubFalso(httpx.Response(201, json={"html_url": URL_PR, "draft": True}))
    pr = crear_pull_request(PEDIDO, TOKEN, github.transporte)

    assert pr == PullRequest(URL_PR, es_borrador=True)
    (pedido,) = github.pedidos
    assert pedido.method == "POST" and pedido.url.path == RUTA_PULLS
    assert pedido.headers["Authorization"] == f"Bearer {TOKEN}"
    assert pedido.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert github.cuerpo(0) == {
        "title": "T-1: Crear tareas",
        "head": "feature/T-1",
        "base": "main",
        "body": "## Resumen",
        "draft": True,
    }


def test_si_el_repo_no_admite_borradores_crea_un_pr_normal() -> None:
    github = GitHubFalso(
        _error_422("Draft pull requests are not supported in this repository."),
        httpx.Response(201, json={"html_url": URL_PR, "draft": False}),
    )
    pr = crear_pull_request(PEDIDO, TOKEN, github.transporte)

    assert pr == PullRequest(URL_PR, es_borrador=False, es_borrador_rechazado=True)
    assert [github.cuerpo(i)["draft"] for i in range(2)] == [True, False]


def test_si_el_pr_ya_existe_devuelve_ese() -> None:
    github = GitHubFalso(
        _error_422("A pull request already exists for gerson:feature/T-1."),
        httpx.Response(200, json=[{"html_url": URL_PR, "draft": True}]),
    )
    pr = crear_pull_request(PEDIDO, TOKEN, github.transporte)

    assert pr == PullRequest(URL_PR, es_borrador=True, es_existente=True)
    busqueda = github.pedidos[1]
    assert busqueda.method == "GET"
    assert busqueda.url.params["head"] == "gerson:feature/T-1"


@pytest.mark.parametrize(
    ("respuesta", "detalle"),
    [
        (httpx.Response(401, json={"message": "Bad credentials"}), "401 Bad credentials"),
        (
            httpx.Response(403, json={"message": "Resource not accessible by personal token"}),
            "403 Resource not accessible",
        ),
        (
            _error_422("No commits between main and feature/T-1"),
            "422 Validation Failed; No commits between",
        ),
    ],
)
def test_otros_errores_de_github(respuesta: httpx.Response, detalle: str) -> None:
    with pytest.raises(ErrorGitHub, match=f"GitHub no creó el pull request: {detalle}"):
        crear_pull_request(PEDIDO, TOKEN, GitHubFalso(respuesta).transporte)


def test_sin_conexion() -> None:
    def sin_red(pedido: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=pedido)

    with pytest.raises(ErrorGitHub, match="No se pudo conectar con GitHub"):
        crear_pull_request(PEDIDO, TOKEN, httpx.MockTransport(sin_red))
