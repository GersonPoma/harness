import json
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from harness import jira_mcp
from harness.cliente_mcp import SesionMCP
from harness.config import ConfigJira
from harness.config_mcp import ServidorStdio
from harness.jira import ErrorJira, obtener_cloud_id
from harness.jira_mcp import herramientas_usadas, preparar_comentarios
from tests.datos.servidor_mcp_jira_falso import (
    TICKET_SIN_PERMISO,
    VAR_COMENTARIOS,
    VAR_ESTILO,
)
from tests.mcp_prueba import servidor_stdio

RUTA_SERVIDOR = Path(__file__).parent / "datos" / "servidor_mcp_jira_falso.py"
CONFIG = ConfigJira("https://ejemplo.atlassian.net", "yo@ejemplo.com", "jira-token-falso-123")
URL_PR = "https://github.com/o/r/pull/7"


def servidor_jira(estilo: str, comentarios: Path) -> ServidorStdio:
    """El servidor falso de Jira como proceso hijo."""
    env = {VAR_ESTILO: estilo, VAR_COMENTARIOS: str(comentarios)}
    return ServidorStdio("jira", sys.executable, (str(RUTA_SERVIDOR),), env)


@pytest.fixture(params=["remoto", "docker"])
def sesion(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[SesionMCP, Path]]:
    # El remoto pide el cloudId: en los tests no se consulta el sitio.
    monkeypatch.setattr(jira_mcp, "obtener_cloud_id", lambda config: "cloud-falso")
    comentarios = tmp_path / "comentarios.json"
    servidor = servidor_jira(request.param, comentarios)
    with SesionMCP(servidor, tmp_path / "jira.log", "T-1") as abierta:
        yield abierta, comentarios


def test_comenta_y_despues_lo_encuentra(sesion: tuple[SesionMCP, Path]) -> None:
    abierta, archivo = sesion
    comentarios = preparar_comentarios(abierta, CONFIG)

    assert comentarios is not None
    assert not comentarios.tiene_texto("TAREAS-1", URL_PR)
    comentarios.comentar("TAREAS-1", f"Pull request: [{URL_PR}]({URL_PR})")
    assert comentarios.tiene_texto("TAREAS-1", URL_PR)
    (guardado,) = json.loads(archivo.read_text(encoding="utf-8"))["TAREAS-1"]
    assert guardado["texto"] == f"Pull request: {URL_PR}" and guardado["enlaces"] == [URL_PR]


def test_el_falso_deja_la_url_suelta_como_texto(sesion: tuple[SesionMCP, Path]) -> None:
    # Como sooperset en la prueba real: solo [texto](url) queda como enlace en Jira.
    abierta, archivo = sesion
    comentarios = preparar_comentarios(abierta, CONFIG)
    for cuerpo in (f"PR: {URL_PR}", f"PR: <{URL_PR}>", f"PR: [{URL_PR}|{URL_PR}]"):
        comentarios.comentar("TAREAS-1", cuerpo)
    guardados = json.loads(archivo.read_text(encoding="utf-8"))["TAREAS-1"]
    assert [guardado["enlaces"] for guardado in guardados] == [[], [], []]


def test_el_remoto_usa_el_cloud_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jira_mcp, "obtener_cloud_id", lambda config: "cloud-falso")
    archivo = tmp_path / "comentarios.json"
    with SesionMCP(servidor_jira("remoto", archivo), tmp_path / "j.log", "T-1") as abierta:
        preparar_comentarios(abierta, CONFIG).comentar("TAREAS-1", "hola")
    (guardado,) = json.loads(archivo.read_text(encoding="utf-8"))["TAREAS-1"]
    assert guardado["cuerpo"] == "hola" and guardado["cloudId"] == "cloud-falso"


def test_un_comentario_rechazado_da_error_jira(sesion: tuple[SesionMCP, Path]) -> None:
    abierta, _ = sesion
    with pytest.raises(ErrorJira, match="no tiene permiso|do not have permission"):
        preparar_comentarios(abierta, CONFIG).comentar(TICKET_SIN_PERMISO, "x")


def test_herramientas_usadas_solo_las_de_comentarios(sesion: tuple[SesionMCP, Path]) -> None:
    abierta, _ = sesion
    usadas = herramientas_usadas(abierta.herramientas)
    assert len(usadas) == 2 and "jira_search" not in usadas


def test_un_servidor_sin_esas_herramientas_no_sirve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with SesionMCP(servidor_stdio(), tmp_path / "g.log", "T-1") as abierta:
        assert preparar_comentarios(abierta, CONFIG) is None


# --- cloudId del sitio --------------------------------------------------------------------


def test_obtener_cloud_id() -> None:
    def responder(pedido: httpx.Request) -> httpx.Response:
        assert str(pedido.url) == "https://ejemplo.atlassian.net/_edge/tenant_info"
        # Público: no lleva credenciales.
        assert "Authorization" not in pedido.headers
        return httpx.Response(200, json={"cloudId": "abc-123"})

    assert obtener_cloud_id(CONFIG, httpx.MockTransport(responder)) == "abc-123"


@pytest.mark.parametrize(
    "respuesta", [httpx.Response(404, text="no"), httpx.Response(200, json={"otro": 1})]
)
def test_obtener_cloud_id_con_error(respuesta: httpx.Response) -> None:
    with pytest.raises(ErrorJira, match="No se pudo obtener el cloudId"):
        obtener_cloud_id(CONFIG, httpx.MockTransport(lambda _: respuesta))
