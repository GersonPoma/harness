"""harness run y harness ticket con una clave de Jira (API REST simulada con el ADF real)."""

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from harness import cli, jira
from harness.config import ConfigJira
from harness.prompts import crear_mensajes_iniciales
from harness.ticket import MAX_CARACTERES_DESCRIPCION
from harness.tipos import Rol
from tests.falsos import ProveedorFalso, terminar
from tests.git_prueba import git, requiere_git
from tests.test_cli import (  # noqa: F401 - repo_sin_yaml es un fixture
    _correr,
    _correr_con_entrega,
    _eventos_del_log,
    repo_sin_yaml,
)

DATOS_JIRA = Path(__file__).parent / "datos" / "jira"
TOKEN_JIRA = "jira-token-secreto"
AUTOR = {"accountId": "712020:aaaa", "emailAddress": "autor@ejemplo.com",
         "displayName": "Autor Secreto", "avatarUrls": {"48x48": "https://avatar.ejemplo/a.png"}}


def _issue(numero: int) -> dict:
    datos = json.loads((DATOS_JIRA / f"TAREAS-{numero}.json").read_text(encoding="utf-8"))
    # Lo que Jira mandaría del autor: el harness no debe dejarlo pasar a ningún lado.
    datos["fields"]["reporter"] = AUTOR
    return datos


def _con_jira(
    monkeypatch: pytest.MonkeyPatch,
    issue: dict | None = None,
    respuesta_myself: httpx.Response | None = None,
    respuesta_issue: httpx.Response | None = None,
) -> list[httpx.Request]:
    """Jira simulado: /myself y el issue. Devuelve los pedidos que recibió."""
    monkeypatch.setenv("JIRA_URL", "https://ejemplo.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "yo@ejemplo.com")
    monkeypatch.setenv("JIRA_API_TOKEN", TOKEN_JIRA)
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        if pedido.url.path.endswith("/myself"):
            return respuesta_myself or httpx.Response(200, json={"accountId": "yo"})
        return respuesta_issue or httpx.Response(200, json=issue or _issue(3))

    transporte = httpx.MockTransport(responder)
    original = jira._pedir

    def pedir_falso(config: ConfigJira, metodo: str, ruta: str, _transporte, **opciones):
        return original(config, metodo, ruta, transporte, **opciones)

    monkeypatch.setattr(jira, "_pedir", pedir_falso)
    return pedidos


def _sistema_y_usuario(proveedor: ProveedorFalso) -> tuple[str, str]:
    mensajes = proveedor.conversaciones[0]
    sistema = next(m.contenido for m in mensajes if m.rol == Rol.SISTEMA)
    usuario = next(m.contenido for m in mensajes if m.rol == Rol.USUARIO)
    return sistema, usuario


def test_run_con_clave_de_jira_le_da_al_modelo_el_ticket_normalizado(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch)
    proveedor = ProveedorFalso([terminar(f"Usé {TOKEN_JIRA} sin querer")])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-plan", ticket="TAREAS-3")

    assert codigo == 0
    assert [p.url.path for p in pedidos] == [
        "/rest/api/3/myself", "/rest/api/3/issue/TAREAS-3",
    ]
    _, usuario = _sistema_y_usuario(proveedor)
    esperado = (
        "# TAREAS-3: Completar tareas y filtrarlas\n\n**Tipo:** Tarea\n**Prioridad:** Medium\n"
        "**Estado:** Por hacer\n\n"
        + (DATOS_JIRA / "TAREAS-3.md").read_text(encoding="utf-8").strip()
    )
    assert usuario == f"Implementa el siguiente ticket:\n\n{esperado}"
    salida_plana = " ".join(salida.split())
    assert "Jira TAREAS-3: Completar tareas y filtrarlas (leído por la API REST)" in salida_plana
    assert "https://ejemplo.atlassian.net/browse/TAREAS-3" in salida_plana

    eventos = _eventos_del_log(repo_sin_yaml)
    (ticket,) = [e for e in eventos if e["evento"] == "ticket"]
    assert ticket == {**ticket, "ticket": "TAREAS-3", "fuente": "jira", "es_recortado": False,
                      "url": "https://ejemplo.atlassian.net/browse/TAREAS-3"}
    log = json.dumps(eventos, ensure_ascii=False)
    # El token de Jira se oculta, y nada del autor llega al log ni al modelo.
    assert TOKEN_JIRA not in log and "****" in log
    for dato in ("712020:aaaa", "autor@ejemplo.com", "Autor Secreto", "avatar.ejemplo"):
        assert dato not in log and dato not in usuario


def test_la_clave_en_minusculas_tambien_es_de_jira(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch)
    codigo, _ = _correr(
        monkeypatch, repo_sin_yaml, [terminar()], "--sin-plan", ticket="tareas-3"
    )
    assert codigo == 0 and pedidos[-1].url.path == "/rest/api/3/issue/TAREAS-3"


@requiere_git
def test_rama_y_commit_con_la_clave_y_el_titulo_de_jira(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_jira(monkeypatch, _issue(5))
    codigo, salida, _ = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True, "--sin-push", ticket="TAREAS-5"
    )
    assert codigo == 0
    assert "Rama feature/TAREAS-5 creada desde main." in salida
    assert git(repo_sin_yaml, "log", "-1", "--format=%B", "feature/TAREAS-5") == (
        "TAREAS-5: Eliminar tareas"
    )


@pytest.mark.parametrize(
    ("respuestas", "mensaje"),
    [
        ({"respuesta_myself": httpx.Response(401)}, "Jira rechazó JIRA_EMAIL o JIRA_API_TOKEN"),
        ({"respuesta_issue": httpx.Response(404, json={"errorMessages": ["no existe"]})},
         "No existe TAREAS-3 o tu usuario no tiene permiso para verlo"),
    ],
)
@requiere_git
def test_si_jira_falla_se_detiene_sin_crear_la_rama(
    monkeypatch: pytest.MonkeyPatch,
    repo_sin_yaml: Path,  # noqa: F811
    respuestas: dict,
    mensaje: str,
) -> None:
    _con_jira(monkeypatch, **respuestas)
    codigo, salida, _ = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True, "--sin-push", ticket="TAREAS-3"
    )
    assert codigo == 1 and mensaje in " ".join(salida.split())
    assert git(repo_sin_yaml, "branch", "--list", "feature/TAREAS-3") == ""
    assert not (repo_sin_yaml / "tareas.py").exists()


def test_sin_credenciales_de_jira_dice_cuales_faltan(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    for variable in ("JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(variable, raising=False)
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, ticket="TAREAS-3")
    assert codigo == 1 and proveedor.conversaciones == []
    assert "falta JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN en el .env" in " ".join(salida.split())


def test_ni_archivo_ni_clave(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [terminar()], ticket="tickets/no.md")
    assert codigo == 1
    assert "No existe el archivo 'tickets/no.md' y no parece una clave de Jira" in salida


def test_una_descripcion_enorme_se_recorta_y_se_avisa(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    texto = "y" * (MAX_CARACTERES_DESCRIPCION + 1000)
    issue = {"fields": {"summary": "Grande", "description": {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": texto}]}]}}}
    _con_jira(monkeypatch, issue)
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-plan", ticket="T-9")

    assert codigo == 0
    aviso = (f"La descripción de T-9 tiene {len(texto)} caracteres: el modelo recibe los "
             f"primeros {MAX_CARACTERES_DESCRIPCION}.")
    assert aviso in " ".join(salida.split())
    _, usuario = _sistema_y_usuario(proveedor)
    assert len(usuario) < MAX_CARACTERES_DESCRIPCION + 300
    assert usuario.endswith("caracteres de la descripción ...]")


def test_un_ticket_local_sigue_igual(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch)
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-plan")

    assert codigo == 0 and pedidos == []
    _, usuario = _sistema_y_usuario(proveedor)
    assert usuario == "Implementa el siguiente ticket:\n\n# T-1: algo"
    assert "Ticket T-1 desde un archivo local." in salida


# --- harness ticket -----------------------------------------------------------------------


def test_harness_ticket_con_clave_muestra_lo_que_recibe_el_modelo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _con_jira(monkeypatch, _issue(5))
    monkeypatch.setattr(cli, "cargar_env", lambda: None)
    resultado = CliRunner().invoke(cli.app, ["ticket", "TAREAS-5"])

    assert resultado.exit_code == 0
    assert "# TAREAS-5: Eliminar tareas\n\n**Tipo:** Tarea" in resultado.output
    assert '```json\n{"detail": "Tarea no encontrada"}\n```' in resultado.output
    assert "Autor Secreto" not in resultado.output


def test_harness_ticket_con_un_md_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ruta = tmp_path / "TAREAS-3.md"
    ruta.write_text("# TAREAS-3: Completar\n\n- [x] Hecho\n", encoding="utf-8")
    monkeypatch.setattr(cli, "cargar_env", lambda: None)
    resultado = CliRunner().invoke(cli.app, ["ticket", str(ruta)])

    assert resultado.exit_code == 0
    assert "Ticket TAREAS-3 desde un archivo local." in resultado.output
    assert "# TAREAS-3: Completar\n\n- [x] Hecho" in resultado.output


def test_harness_ticket_con_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _con_jira(monkeypatch, respuesta_myself=httpx.Response(401))
    monkeypatch.setattr(cli, "cargar_env", lambda: None)
    resultado = CliRunner().invoke(cli.app, ["ticket", "TAREAS-5"])
    assert resultado.exit_code == 1 and "Jira rechazó" in resultado.output


# --- El prompt ----------------------------------------------------------------------------


def test_el_prompt_explica_los_criterios_marcados_como_hechos() -> None:
    (sistema, _) = crear_mensajes_iniciales("ticket")
    contenido = " ".join(sistema.contenido.split())
    assert 'Los criterios marcados como hechos ("- [x]") son cosas que el ticket da por ' in (
        contenido
    )
    assert "Verifica en el código que de verdad lo estén: si lo están, no las rehagas; si no, " \
        "trátalas como pendientes." in contenido
    assert "Agrega o actualiza siempre los tests de lo que cambias, aunque el criterio de " \
        "tests esté marcado como hecho." in contenido
