"""harness run y harness ticket con una clave de Jira (API REST simulada con el ADF real)."""

import json
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from harness import cli, jira
from harness.config import ConfigJira
from harness.github import PullRequest
from harness.jira import comentario_adf
from harness.prompts import crear_mensajes_iniciales
from harness.ticket import MAX_CARACTERES_DESCRIPCION
from harness.tipos import Rol
from tests.datos.servidor_mcp_jira_falso import VAR_COMENTARIOS, VAR_ESTILO
from tests.falsos import ProveedorFalso, terminar
from tests.git_prueba import git, requiere_git
from tests.test_cli import (  # noqa: F401 - repo_sin_yaml es un fixture
    _con_github_al_empezar,
    _correr,
    _correr_con_entrega,
    _eventos_del_log,
    repo_sin_yaml,
)

DATOS_JIRA = Path(__file__).parent / "datos" / "jira"
RUTA_SERVIDOR_JIRA = Path(__file__).parent / "datos" / "servidor_mcp_jira_falso.py"
RUTA_SERVIDOR_GITHUB = Path(__file__).parent / "datos" / "servidor_mcp_falso.py"
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
    comentarios: list[str] | None = None,
    respuesta_comentar: httpx.Response | None = None,
) -> list[httpx.Request]:
    """Jira simulado: /myself, el issue y sus comentarios. Devuelve los pedidos que recibió.

    `comentarios` son los textos que ya tiene el ticket; los que se agregan se suman ahí.
    """
    monkeypatch.setenv("JIRA_URL", "https://ejemplo.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "yo@ejemplo.com")
    monkeypatch.setenv("JIRA_API_TOKEN", TOKEN_JIRA)
    pedidos: list[httpx.Request] = []
    existentes = comentarios if comentarios is not None else []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        if pedido.url.path.endswith("/myself"):
            return respuesta_myself or httpx.Response(200, json={"accountId": "yo"})
        if pedido.url.path.endswith("/comment") and pedido.method == "GET":
            cuerpos = [{"body": comentario_adf(texto, "")} for texto in existentes]
            return httpx.Response(200, json={"comments": cuerpos, "total": len(cuerpos)})
        if pedido.url.path.endswith("/comment"):
            return respuesta_comentar or httpx.Response(201, json={"id": "1"})
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
    assert "falta JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN: guárdalo con 'harness config'" in (
        " ".join(salida.split())
    )


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


@pytest.mark.parametrize("ticket", ["# TAREAS-4: De Jira\n\n**Tipo:** Tarea", "# Ticket local"])
def test_el_ticket_del_mensaje_es_la_fuente_de_verdad(ticket: str) -> None:
    # En la prueba real el modelo leyó tickets/TAREAS-4.md del repo teniendo ya el de Jira.
    (sistema, _) = crear_mensajes_iniciales(ticket)
    indicacion = (
        "El ticket completo ya está en el mensaje del usuario. No busques el ticket en archivos "
        "del repositorio; si encuentras una copia, la del mensaje es la que vale."
    )
    assert indicacion in " ".join(sistema.contenido.split())


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


# --- MCP de Jira en el preflight -----------------------------------------------------------


def _mcp_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, servidores: dict) -> None:
    ruta = tmp_path / "mcp.json"
    ruta.write_text(json.dumps({"mcpServers": servidores}), encoding="utf-8")
    monkeypatch.setattr(cli, "ruta_mcp", lambda: ruta)


def _jira_falso(tmp_path: Path, estilo: str = "docker") -> dict:
    return {
        "command": sys.executable,
        "args": [str(RUTA_SERVIDOR_JIRA)],
        "env": {VAR_ESTILO: estilo, VAR_COMENTARIOS: str(tmp_path / "comentarios.json")},
    }


@requiere_git
def test_jira_por_mcp_en_el_preflight_sin_herramientas_para_el_modelo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_jira(monkeypatch)
    github = {"command": sys.executable, "args": [str(RUTA_SERVIDOR_GITHUB)]}
    _mcp_json(monkeypatch, tmp_path, {"github": github, "jira": _jira_falso(tmp_path)})
    servidores_con_herramientas: list[str] = []
    original = cli.crear_herramientas_mcp

    def espiar(sesion, *argumentos):
        servidores_con_herramientas.append(sesion.servidor.nombre)
        return original(sesion, *argumentos)

    monkeypatch.setattr(cli, "crear_herramientas_mcp", espiar)
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")

    assert codigo == 0
    salida_plana = " ".join(salida.split())
    aviso = "Jira por MCP (servidor jira, stdio): el link del PR se comenta por MCP."
    assert salida_plana.index(aviso) < salida_plana.index("Rama feature/TAREAS-3 creada")
    # Las herramientas del modelo salen solo del servidor de GitHub.
    assert servidores_con_herramientas == ["github"]
    eventos = {e["servidor"]: e for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "mcp"}
    assert all(h.startswith("github__") for h in eventos["github"]["herramientas"])
    assert eventos["jira"]["herramientas"] == []
    assert eventos["jira"]["usadas_por_el_harness"] == ["jira_get_issue", "jira_add_comment"]


@requiere_git
def test_sin_servidor_de_jira_se_comenta_por_rest(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_jira(monkeypatch)
    _, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")
    assert (
        "Jira por la API REST: el link del PR se comenta por REST (no hay servidor MCP de Jira "
        "en mcp.json)." in " ".join(salida.split())
    )


@requiere_git
def test_si_el_servidor_de_jira_no_conecta_se_comenta_por_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_jira(monkeypatch)
    _mcp_json(monkeypatch, tmp_path, {"jira": {"command": "comando-que-no-existe-xyz"}})
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")
    assert codigo == 0
    salida_plana = " ".join(salida.split())
    assert "No se pudo conectar con el servidor MCP 'jira'" in salida_plana
    assert "El link del PR se comenta por la API REST de Jira." in salida_plana


@requiere_git
def test_con_sin_pr_ni_se_conecta_el_servidor_de_jira(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    _con_jira(monkeypatch)
    _mcp_json(monkeypatch, tmp_path, {"jira": _jira_falso(tmp_path)})
    _, salida, _ = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True, "--sin-pr", ticket="TAREAS-3"
    )
    assert "Jira por" not in salida
    assert not [e for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "mcp"]


# --- Secretos demasiado cortos -------------------------------------------------------------


def test_un_secreto_corto_se_avisa_y_no_rompe_el_log(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    # "ticket" es un campo del log: si el token fuera esa palabra, ocultarlo lo rompería.
    monkeypatch.setenv("GITHUB_TOKEN", "ticket")
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [terminar()], "--sin-plan")

    assert codigo == 0
    assert (
        "El valor de GITHUB_TOKEN es demasiado corto para ser un token real: revisa "
        "GITHUB_TOKEN (viene del entorno)."
        in " ".join(salida.split())
    )
    (evento,) = [e for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "ticket"]
    assert evento["ticket"] == "T-1"


@requiere_git
def test_una_variable_corta_de_harness_yaml_tiene_su_propio_aviso(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    monkeypatch.setenv("CLAVE_DB_PRUEBA", "test")
    yaml_extra = 'variables_entorno:\n  DB_PASSWORD: "${CLAVE_DB_PRUEBA}"\n'
    codigo, salida, _ = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True, "--sin-push", yaml_extra=yaml_extra
    )
    salida_plana = " ".join(salida.split())

    assert codigo == 0
    aviso = (
        "El valor de CLAVE_DB_PRUEBA (harness.yaml) es corto y no se oculta en la consola ni "
        "en el log; si es un secreto real, usa uno de al menos 8 caracteres."
    )
    assert salida_plana.count(aviso) == 1
    assert "CLAVE_DB_PRUEBA es demasiado corto para ser un token real" not in salida_plana


# --- Comentario con el link del PR --------------------------------------------------------

URL_PR = "https://github.com/gerson/demo/pull/3"


def _con_pr(monkeypatch: pytest.MonkeyPatch, es_borrador: bool = True) -> None:
    """El PR se crea por la API REST (simulada) en un remoto que parece de GitHub."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-falso-0123")
    _con_github_al_empezar(monkeypatch)
    pr = PullRequest(URL_PR, es_borrador=es_borrador, es_borrador_rechazado=not es_borrador)
    monkeypatch.setattr("harness.entrega.github.crear_pull_request", lambda pedido, token: pr)


def _comentarios_enviados(pedidos: list[httpx.Request]) -> list[dict]:
    return [json.loads(p.content)["body"] for p in pedidos
            if p.method == "POST" and p.url.path.endswith("/comment")]


@requiere_git
def test_despues_del_pr_comenta_el_link_por_rest(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch)
    _con_pr(monkeypatch)
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")

    assert codigo == 0
    assert _comentarios_enviados(pedidos) == [
        comentario_adf("Pull request en borrador creado por harness: ", URL_PR)
    ]
    salida_plana = " ".join(salida.split())
    assert ("✓ Comentario en TAREAS-3 (por la API REST): "
            "https://ejemplo.atlassian.net/browse/TAREAS-3") in salida_plana
    (evento,) = [e for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "jira_comentario"]
    assert (evento["estado"], evento["camino"]) == ("comentado", "rest")


@requiere_git
def test_si_el_borrador_no_se_admitio_el_comentario_no_dice_borrador(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch)
    _con_pr(monkeypatch, es_borrador=False)
    _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")
    (comentario,) = _comentarios_enviados(pedidos)
    assert comentario["content"][0]["content"][0]["text"] == "Pull request creado por harness: "


@requiere_git
def test_si_el_ticket_ya_tiene_el_link_no_comenta_otra_vez(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch, comentarios=[f"Pull request en borrador: {URL_PR}"])
    _con_pr(monkeypatch)
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")

    assert codigo == 0 and _comentarios_enviados(pedidos) == []
    assert "El ticket TAREAS-3 ya tiene el link del PR: no se comenta otra vez." in (
        " ".join(salida.split())
    )


@requiere_git
def test_si_el_comentario_falla_solo_avisa(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    rechazo = httpx.Response(403, json={"errorMessages": ["No tienes permiso para comentar."]})
    _con_jira(monkeypatch, respuesta_comentar=rechazo)
    _con_pr(monkeypatch)
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")

    # El código está validado, subido y el PR existe: el comentario no cambia el resultado.
    assert codigo == 0
    salida_plana = " ".join(salida.split())
    assert "No se pudo comentar en TAREAS-3: Jira no agregó el comentario" in salida_plana
    assert f"Agrégalo a mano: {URL_PR}" in salida_plana


@requiere_git
@pytest.mark.parametrize("opcion", ["--sin-pr", "--sin-push"])
def test_sin_pr_no_comenta(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path, opcion: str  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch)
    _con_pr(monkeypatch)
    codigo, salida, _ = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True, opcion, ticket="TAREAS-3"
    )
    assert codigo == 0 and "Comentario" not in salida
    assert not [p for p in pedidos if p.url.path.endswith("/comment")]


@requiere_git
def test_un_ticket_local_no_comenta(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch)
    _con_pr(monkeypatch)
    codigo, _, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True)
    assert codigo == 0 and pedidos == []


@requiere_git
def test_comenta_por_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    pedidos = _con_jira(monkeypatch)
    _con_pr(monkeypatch)
    _mcp_json(monkeypatch, tmp_path, {"jira": _jira_falso(tmp_path)})
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")

    assert codigo == 0 and _comentarios_enviados(pedidos) == []
    (guardado,) = json.loads((tmp_path / "comentarios.json").read_text(encoding="utf-8"))[
        "TAREAS-3"
    ]
    # En Jira queda el texto con la URL del PR como enlace, no como texto plano.
    assert guardado["texto"] == f"Pull request en borrador creado por harness: {URL_PR}"
    assert guardado["enlaces"] == [URL_PR]
    assert "✓ Comentario en TAREAS-3 (por MCP)" in " ".join(salida.split())


@requiere_git
def test_harness_mcp_jira_rest_fuerza_la_api_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_sin_yaml: Path  # noqa: F811
) -> None:
    monkeypatch.setenv("HARNESS_MCP_JIRA", "rest")
    pedidos = _con_jira(monkeypatch)
    _con_pr(monkeypatch)
    _mcp_json(monkeypatch, tmp_path, {"jira": _jira_falso(tmp_path)})
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, ticket="TAREAS-3")

    assert codigo == 0
    salida_plana = " ".join(salida.split())
    assert ("Jira por la API REST: el link del PR se comenta por REST "
            "(HARNESS_MCP_JIRA=rest).") in salida_plana
    assert "✓ Comentario en TAREAS-3 (por la API REST)" in salida_plana
    assert len(_comentarios_enviados(pedidos)) == 1
    assert not (tmp_path / "comentarios.json").exists()


def test_harness_ticket_no_usa_el_indicador_animado(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lo que sigue es imprimir el ticket entero: no hay nada que animar ni que borrar.
    _con_jira(monkeypatch, _issue(1))
    monkeypatch.setattr(cli, "cargar_env", lambda: None)
    usados: list[str] = []
    monkeypatch.setattr(cli.consola, "estado", lambda mensaje: usados.append(mensaje))
    resultado = CliRunner().invoke(cli.app, ["ticket", "TAREAS-1"])
    assert resultado.exit_code == 0 and usados == []


# --- Mover el ticket al abrir el PR -------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from harness.config_proyecto import validar_texto_config  # noqa: E402
from harness.jira import MOVIDO, ErrorJira, ResultadoTransicion  # noqa: E402
from harness.registro import RegistroPasos  # noqa: E402

YAML_JIRA = (
    "version: 1\nproyecto: p\ndocker:\n  imagen: python:3.12\ncomandos:\n  tests: pytest\n"
    "jira:\n  estado_al_abrir_pr: En revisión\n"
)


def _mover(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, yaml: str, efecto) -> tuple:
    pedidos: list[tuple] = []

    def mover_falso(config, clave, estado):
        pedidos.append((clave, estado))
        if isinstance(efecto, Exception):
            raise efecto
        return efecto

    monkeypatch.setattr(cli, "mover_ticket", mover_falso)
    prep = SimpleNamespace(
        config_proyecto=validar_texto_config(yaml), config_jira=object(), ticket="TAREAS-3"
    )
    registro = RegistroPasos(tmp_path, "TAREAS-3", secretos=[])
    cli._mover_en_jira(prep, cli._Reporte(registro))
    texto = registro.ruta.read_text("utf-8") if registro.ruta.exists() else ""
    eventos = [json.loads(linea) for linea in texto.splitlines()]
    return pedidos, eventos


def test_mueve_el_ticket_si_harness_yaml_lo_pide(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pedidos, eventos = _mover(monkeypatch, tmp_path, YAML_JIRA,
                              ResultadoTransicion(MOVIDO, "En curso"))
    assert pedidos == [("TAREAS-3", "En revisión")]
    assert eventos[-1]["evento"] == "jira_transicion" and eventos[-1]["resultado"] == MOVIDO


def test_sin_la_clave_no_se_mueve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sin_clave = YAML_JIRA.split("jira:")[0]
    pedidos, eventos = _mover(monkeypatch, tmp_path, sin_clave, ResultadoTransicion(MOVIDO))
    assert pedidos == [] and eventos == []


def test_si_jira_falla_solo_se_avisa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pedidos, eventos = _mover(monkeypatch, tmp_path, YAML_JIRA, ErrorJira("Jira no responde"))
    assert eventos[-1]["resultado"] == "error" and eventos[-1]["error"] == "Jira no responde"
