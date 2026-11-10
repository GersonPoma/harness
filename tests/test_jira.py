import json
from pathlib import Path

import httpx
import pytest

from harness.config import ConfigJira
from harness.jira import (
    ErrorJira,
    comentar,
    leer_comentarios,
    leer_ticket,
    url_ticket,
    verificar_credenciales,
)
from harness.ticket import MAX_CARACTERES_DESCRIPCION

CONFIG = ConfigJira("https://ejemplo.atlassian.net", "yo@ejemplo.com", "token-secreto")
DATOS_JIRA = Path(__file__).parent / "datos" / "jira"
# Lo que traería Jira del autor si se pidieran todos los campos: nunca debe llegar al Ticket.
AUTOR = {
    "accountId": "712020:aaaa-bbbb",
    "emailAddress": "autor@ejemplo.com",
    "displayName": "Autor Secreto",
    "avatarUrls": {"48x48": "https://avatar.ejemplo.com/autor.png"},
}


class JiraFalso:
    """Responde en orden y guarda los pedidos que recibió."""

    def __init__(self, *respuestas: httpx.Response) -> None:
        self._respuestas = list(respuestas)
        self.pedidos: list[httpx.Request] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        respuesta = self._respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    @property
    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _issue(numero: int, **extra_campos: object) -> dict:
    datos = json.loads((DATOS_JIRA / f"TAREAS-{numero}.json").read_text(encoding="utf-8"))
    datos["fields"].update(extra_campos)
    return datos


# --- Credenciales -------------------------------------------------------------------------


def test_verificar_credenciales_ok() -> None:
    jira = JiraFalso(httpx.Response(200, json={"accountId": "x", "displayName": "Yo"}))
    verificar_credenciales(CONFIG, jira.transporte)

    (pedido,) = jira.pedidos
    assert str(pedido.url) == "https://ejemplo.atlassian.net/rest/api/3/myself"
    # Basic con email y token, como pide Jira Cloud.
    assert pedido.headers["Authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    ("respuesta", "mensaje"),
    [
        (httpx.Response(401, text="Unauthorized"), "Jira rechazó JIRA_EMAIL o JIRA_API_TOKEN"),
        (httpx.Response(404, text="<html>no</html>"), "no parece un sitio de Jira Cloud"),
        (httpx.Response(200, text="<html>login</html>"), "no parece un sitio de Jira Cloud"),
    ],
)
def test_verificar_credenciales_dice_que_falta(respuesta: httpx.Response, mensaje: str) -> None:
    with pytest.raises(ErrorJira, match=mensaje):
        verificar_credenciales(CONFIG, JiraFalso(respuesta).transporte)


def test_sin_conexion() -> None:
    sin_red = httpx.ConnectError("sin red")
    with pytest.raises(ErrorJira, match=r"No se pudo conectar con Jira \(https://ejemplo"):
        verificar_credenciales(CONFIG, JiraFalso(sin_red).transporte)


# --- Leer el ticket -----------------------------------------------------------------------


def test_leer_ticket_real_normalizado() -> None:
    jira = JiraFalso(httpx.Response(200, json=_issue(3)))
    ticket = leer_ticket(CONFIG, "TAREAS-3", jira.transporte)

    (pedido,) = jira.pedidos
    assert pedido.url.path == "/rest/api/3/issue/TAREAS-3"
    assert pedido.url.params["fields"] == (
        "summary,description,issuetype,priority,status,issuelinks,subtasks,parent"
    )
    assert ticket.clave == "TAREAS-3" and ticket.titulo == "Completar tareas y filtrarlas"
    assert (ticket.tipo, ticket.prioridad, ticket.estado) == ("Tarea", "Medium", "Por hacer")
    assert ticket.url == "https://ejemplo.atlassian.net/browse/TAREAS-3"
    assert ticket.descripcion == (DATOS_JIRA / "TAREAS-3.md").read_text(encoding="utf-8").strip()
    assert not ticket.es_recortado


def test_el_ticket_llega_al_modelo_como_un_md_local() -> None:
    jira = JiraFalso(httpx.Response(200, json=_issue(3)))
    markdown = leer_ticket(CONFIG, "TAREAS-3", jira.transporte).a_markdown()
    assert markdown.startswith(
        "# TAREAS-3: Completar tareas y filtrarlas\n\n**Tipo:** Tarea\n**Prioridad:** Medium\n"
        "**Estado:** Por hacer\n\nComo usuario de la API quiero marcar tareas"
    )
    assert markdown.endswith("- [x] Tests para cada caso.")


ENLACES = [
    {
        "type": {"name": "Relates", "inward": "relates to", "outward": "relates to"},
        "outwardIssue": {
            "key": "TAREAS-2",
            "fields": {"summary": "Crear tareas", "status": {"name": "Finalizada"},
                       "assignee": AUTOR},
        },
    },
    {
        "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
        "inwardIssue": {"key": "TAREAS-9", "fields": {"summary": "Base de datos"}},
    },
    {"type": {"name": "Raro"}, "inwardIssue": {}},
]


def test_los_tickets_relacionados_van_resumidos_al_final() -> None:
    datos = _issue(
        3,
        issuelinks=ENLACES,
        subtasks=[
            {"key": "TAREAS-7", "fields": {"summary": "Filtro", "status": {"name": "Por hacer"}}}
        ],
        parent={"key": "TAREAS-1", "fields": {"summary": "Gestión de tareas"}},
    )
    ticket = leer_ticket(CONFIG, "TAREAS-3", JiraFalso(httpx.Response(200, json=datos)).transporte)
    markdown = ticket.a_markdown()
    assert markdown.endswith(
        "- [x] Tests para cada caso.\n\n## Tickets relacionados\n\n"
        "Solo el título y el estado de cada uno: su detalle no está disponible. No asumas lo "
        "que piden ni lo implementes, salvo que este ticket lo diga.\n\n"
        "- Padre TAREAS-1: Gestión de tareas\n"
        "- relates to TAREAS-2: Crear tareas (Finalizada)\n"
        "- is blocked by TAREAS-9: Base de datos\n"
        "- Subtarea TAREAS-7: Filtro (Por hacer)"
    )
    # Ni la descripción ni las personas de los enlazados.
    assert "autor@ejemplo.com" not in repr(ticket) + markdown


def test_como_mucho_20_relacionados() -> None:
    subtareas = [{"key": f"TAREAS-{n}", "fields": {"summary": f"S{n}"}} for n in range(30)]
    datos = _issue(3, subtasks=subtareas)
    ticket = leer_ticket(CONFIG, "TAREAS-3", JiraFalso(httpx.Response(200, json=datos)).transporte)
    assert len(ticket.relacionados) == 20


def test_sin_relacionados_el_ticket_no_cambia() -> None:
    jira = JiraFalso(httpx.Response(200, json=_issue(3)))
    ticket = leer_ticket(CONFIG, "TAREAS-3", jira.transporte)
    assert "Tickets relacionados" not in ticket.a_markdown()


def test_nada_del_autor_llega_al_ticket() -> None:
    # Aunque Jira lo mandara (no se pide), el Ticket se arma solo con los campos que usa.
    datos = _issue(5, reporter=AUTOR, creator=AUTOR, assignee=AUTOR)
    ticket = leer_ticket(CONFIG, "TAREAS-5", JiraFalso(httpx.Response(200, json=datos)).transporte)
    todo = repr(ticket) + ticket.a_markdown()
    for dato in ("712020:aaaa-bbbb", "autor@ejemplo.com", "Autor Secreto", "avatar.ejemplo"):
        assert dato not in todo


def test_una_descripcion_enorme_se_recorta() -> None:
    texto = "x" * (MAX_CARACTERES_DESCRIPCION + 5000)
    descripcion = {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": texto}]}]}
    datos = {"fields": {"summary": "Grande", "description": descripcion}}
    ticket = leer_ticket(CONFIG, "T-9", JiraFalso(httpx.Response(200, json=datos)).transporte)

    assert ticket.es_recortado and ticket.caracteres_originales == len(texto)
    assert ticket.descripcion.startswith("x" * 100)
    assert ticket.descripcion.endswith(
        f"[... se omitieron 5000 de {len(texto)} caracteres de la descripción ...]"
    )
    assert len(ticket.descripcion) < MAX_CARACTERES_DESCRIPCION + 100


def test_ticket_sin_descripcion_ni_campos_opcionales() -> None:
    datos = {"fields": {"summary": "Solo título", "description": None}}
    ticket = leer_ticket(CONFIG, "T-1", JiraFalso(httpx.Response(200, json=datos)).transporte)
    assert ticket.descripcion == "" and ticket.tipo is None
    assert ticket.a_markdown() == "# T-1: Solo título"


@pytest.mark.parametrize("codigo", [404, 403])
def test_ticket_que_no_existe_o_no_se_puede_ver(codigo: int) -> None:
    respuesta = httpx.Response(
        codigo, json={"errorMessages": ["Issue does not exist or you do not have permission"]}
    )
    with pytest.raises(ErrorJira, match=rf"No existe T-9 o tu usuario no tiene permiso .*{codigo}"):
        leer_ticket(CONFIG, "T-9", JiraFalso(respuesta).transporte)


def test_otro_error_al_leer_el_ticket() -> None:
    respuesta = httpx.Response(400, json={"errors": {"fields": "campo inválido"}})
    with pytest.raises(ErrorJira, match="Jira no devolvió T-1: 400 fields: campo inválido"):
        leer_ticket(CONFIG, "T-1", JiraFalso(respuesta).transporte)


# --- Comentarios --------------------------------------------------------------------------


def _comentario(texto: str, url: str) -> dict:
    return {"body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": texto},
        {"type": "text", "text": url, "marks": [{"type": "link", "attrs": {"href": url}}]},
    ]}]}, "author": AUTOR}


def test_leer_comentarios_en_markdown() -> None:
    url = "https://github.com/o/r/pull/7"
    datos = {"comments": [_comentario("PR: ", url)], "total": 1}
    jira = JiraFalso(httpx.Response(200, json=datos))
    comentarios = leer_comentarios(CONFIG, "T-1", jira.transporte)

    assert comentarios == [f"PR: [{url}]({url})"]
    assert jira.pedidos[0].url.path == "/rest/api/3/issue/T-1/comment"
    assert "autor@ejemplo.com" not in repr(comentarios)


def test_comentar_manda_el_adf() -> None:
    cuerpo = {"type": "doc", "version": 1, "content": []}
    jira = JiraFalso(httpx.Response(201, json={"id": "100"}))
    comentar(CONFIG, "T-1", cuerpo, jira.transporte)

    (pedido,) = jira.pedidos
    assert pedido.method == "POST" and pedido.url.path == "/rest/api/3/issue/T-1/comment"
    assert json.loads(pedido.content) == {"body": cuerpo}


def test_comentar_con_error() -> None:
    respuesta = httpx.Response(400, json={"errorMessages": ["Comment body can not be empty!"]})
    with pytest.raises(ErrorJira, match="no agregó el comentario en T-1: 400 Comment body"):
        comentar(CONFIG, "T-1", {}, JiraFalso(respuesta).transporte)


def test_url_ticket() -> None:
    assert url_ticket(CONFIG, "TAREAS-1") == "https://ejemplo.atlassian.net/browse/TAREAS-1"


def test_la_config_no_muestra_el_token_ni_el_email() -> None:
    assert "token-secreto" not in repr(CONFIG) and "yo@ejemplo.com" not in repr(CONFIG)


@pytest.mark.parametrize("numero", range(1, 6))
def test_los_tickets_reales_traen_sus_datos_y_una_linea_en_blanco(numero: int) -> None:
    jira = JiraFalso(httpx.Response(200, json=_issue(numero)))
    markdown = leer_ticket(CONFIG, f"TAREAS-{numero}", jira.transporte).a_markdown()
    cabecera, descripcion = markdown.split("\n**Estado:** Por hacer\n\n", 1)
    # Los datos, cada uno en su línea, y una línea en blanco antes de la descripción.
    assert cabecera.endswith("**Tipo:** Tarea\n**Prioridad:** Medium")
    assert descripcion == (DATOS_JIRA / f"TAREAS-{numero}.md").read_text(encoding="utf-8").strip()


# --- Mover el ticket al abrir el PR -------------------------------------------------------

from harness.jira import (  # noqa: E402
    MOVIDO,
    SIN_TRANSICION,
    YA_ESTABA_EN_ESTADO,
    mover_ticket,
)

TRANSICIONES = {"transitions": [
    {"id": "21", "name": "Empezar", "to": {"name": "En curso"}},
    {"id": "31", "name": "Pedir revisión", "to": {"name": "En revisión"}},
    {"id": "41", "name": "Terminar", "to": {"name": "Listo"}},
]}


def _estado(nombre: str) -> httpx.Response:
    return httpx.Response(200, json={"fields": {"status": {"name": nombre}}})


def test_mueve_con_la_transicion_que_lleva_al_estado() -> None:
    jira = JiraFalso(_estado("En curso"), httpx.Response(200, json=TRANSICIONES),
                     httpx.Response(204))
    resultado = mover_ticket(CONFIG, "TAREAS-3", "en  REVISION", jira.transporte)
    assert (resultado.estado, resultado.desde) == (MOVIDO, "En curso")
    lectura, transiciones, post = jira.pedidos
    assert lectura.url.params["fields"] == "status"
    assert transiciones.url.path.endswith("/rest/api/3/issue/TAREAS-3/transitions")
    assert post.method == "POST" and json.loads(post.content) == {"transition": {"id": "31"}}


def test_tambien_por_el_nombre_de_la_transicion() -> None:
    jira = JiraFalso(_estado("En curso"), httpx.Response(200, json=TRANSICIONES),
                     httpx.Response(204))
    assert mover_ticket(CONFIG, "T-1", "Pedir revisión", jira.transporte).estado == MOVIDO


def test_si_ya_esta_en_ese_estado_no_pide_nada_mas() -> None:
    jira = JiraFalso(_estado("En Revisión"))
    resultado = mover_ticket(CONFIG, "T-1", "En revisión", jira.transporte)
    assert resultado.estado == YA_ESTABA_EN_ESTADO and len(jira.pedidos) == 1


def test_sin_transicion_a_ese_estado_dice_cuales_hay() -> None:
    jira = JiraFalso(_estado("Por hacer"), httpx.Response(200, json=TRANSICIONES))
    resultado = mover_ticket(CONFIG, "T-1", "Code review", jira.transporte)
    assert resultado.estado == SIN_TRANSICION
    assert resultado.disponibles == ["En curso", "En revisión", "Listo"]


def test_si_jira_rechaza_la_transicion_es_un_error() -> None:
    rechazo = httpx.Response(400, json={"errorMessages": ["Falta el campo Resolución"]})
    jira = JiraFalso(_estado("En curso"), httpx.Response(200, json=TRANSICIONES), rechazo)
    with pytest.raises(ErrorJira, match="Jira no movió T-1 a 'En revisión': 400 Falta el campo"):
        mover_ticket(CONFIG, "T-1", "En revisión", jira.transporte)
