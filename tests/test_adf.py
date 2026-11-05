import json
from pathlib import Path
from typing import Any

import pytest

from harness.adf import adf_a_markdown

DATOS_JIRA = Path(__file__).parent / "datos" / "jira"


def _doc(*bloques: dict) -> dict:
    return {"type": "doc", "version": 1, "content": list(bloques)}


def _texto(texto: str, *marcas: str, **atributos_link: str) -> dict:
    nodo: dict[str, Any] = {"type": "text", "text": texto}
    lista = [{"type": marca} for marca in marcas]
    if atributos_link:
        lista.append({"type": "link", "attrs": atributos_link})
    if lista:
        nodo["marks"] = lista
    return nodo


def _parrafo(*hijos: dict) -> dict:
    return {"type": "paragraph", "content": list(hijos)}


def _item(*bloques: dict) -> dict:
    return {"type": "listItem", "content": list(bloques)}


# --- Los cinco tickets reales (ADF de Jira Cloud, anonimizado) ----------------------------


@pytest.mark.parametrize("numero", range(1, 6))
def test_tickets_reales(numero: int) -> None:
    datos = json.loads((DATOS_JIRA / f"TAREAS-{numero}.json").read_text(encoding="utf-8"))
    esperado = (DATOS_JIRA / f"TAREAS-{numero}.md").read_text(encoding="utf-8").strip()
    assert adf_a_markdown(datos["fields"]["description"]) == esperado


def test_ticket_real_con_casillas_conserva_su_estado() -> None:
    markdown = (DATOS_JIRA / "TAREAS-3.md").read_text(encoding="utf-8")
    assert markdown.count("- [ ] ") == 5 and markdown.count("- [x] ") == 1


def test_ticket_real_con_panel_codigo_enlace_y_mencion() -> None:
    markdown = (DATOS_JIRA / "TAREAS-5.md").read_text(encoding="utf-8")
    assert "> **Info:** Agregar el endpoint DELETE /tareas/{id}." in markdown
    assert "1. Si la tarea existe" in markdown and "5. Tests para cada caso." in markdown
    assert '```json\n{"detail": "Tarea no encontrada"}\n```' in markdown
    assert "[códigos de estado en FastAPI](https://fastapi.tiangolo.com/" in markdown
    assert markdown.strip().endswith("Revisar con @Persona de Prueba")


# --- Nodos uno por uno --------------------------------------------------------------------


def test_titulos_y_parrafos() -> None:
    documento = _doc(
        {"type": "heading", "attrs": {"level": 2}, "content": [_texto("Criterios")]},
        _parrafo(_texto("Uno")),
        _parrafo(_texto("Dos")),
    )
    assert adf_a_markdown(documento) == "## Criterios\n\nUno\n\nDos"


@pytest.mark.parametrize(
    ("marcas", "esperado"),
    [
        (("strong",), "**x**"),
        (("em",), "*x*"),
        (("strike",), "~~x~~"),
        (("code",), "`x`"),
        (("strong", "em"), "***x***"),
        # Dentro del código, el resto de las marcas no se aplica.
        (("code", "strong"), "`x`"),
        (("underline", "textColor"), "x"),
    ],
)
def test_marcas(marcas: tuple[str, ...], esperado: str) -> None:
    assert adf_a_markdown(_doc(_parrafo(_texto("x", *marcas)))) == esperado


def test_enlace_y_enlace_con_negrita() -> None:
    documento = _doc(_parrafo(
        _texto("docs", href="https://ejemplo.com"),
        _texto(" y "),
        _texto("API", "strong", href="https://api.ejemplo.com"),
    ))
    assert adf_a_markdown(documento) == (
        "[docs](https://ejemplo.com) y [**API**](https://api.ejemplo.com)"
    )


def test_salto_de_linea_y_regla() -> None:
    documento = _doc(
        _parrafo(_texto("a"), {"type": "hardBreak"}, _texto("b")), {"type": "rule"}
    )
    assert adf_a_markdown(documento) == "a\nb\n\n---"


def test_listas_anidadas() -> None:
    sublista = {"type": "orderedList", "content": [
        _item(_parrafo(_texto("uno"))), _item(_parrafo(_texto("dos"))),
    ]}
    documento = _doc({"type": "bulletList", "content": [
        _item(_parrafo(_texto("A")), sublista),
        _item(_parrafo(_texto("B"))),
    ]})
    assert adf_a_markdown(documento) == "- A\n  1. uno\n  2. dos\n- B"


def test_lista_numerada_que_empieza_en_otro_numero() -> None:
    documento = _doc({"type": "orderedList", "attrs": {"order": 3}, "content": [
        _item(_parrafo(_texto("tres"))), _item(_parrafo(_texto("cuatro"))),
    ]})
    assert adf_a_markdown(documento) == "3. tres\n4. cuatro"


def test_item_de_lista_con_varias_lineas_mantiene_la_sangria() -> None:
    documento = _doc({"type": "bulletList", "content": [
        _item(_parrafo(_texto("a"), {"type": "hardBreak"}, _texto("b"))),
    ]})
    assert adf_a_markdown(documento) == "- a\n  b"


def test_lista_de_tareas_con_su_estado_y_anidada() -> None:
    anidada = {"type": "taskList", "content": [
        {"type": "taskItem", "attrs": {"state": "DONE"}, "content": [_texto("sub")]},
    ]}
    documento = _doc({"type": "taskList", "content": [
        {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [_texto("pendiente")]},
        {"type": "taskItem", "attrs": {"state": "DONE"}, "content": [_texto("hecha", "code")]},
        anidada,
    ]})
    assert adf_a_markdown(documento) == "- [ ] pendiente\n- [x] `hecha`\n  - [x] sub"


def test_tabla_con_cabecera_y_barra_escapada() -> None:
    def celda(tipo: str, texto: str) -> dict:
        return {"type": tipo, "content": [_parrafo(_texto(texto))]}

    documento = _doc({"type": "table", "content": [
        {"type": "tableRow", "content": [celda("tableHeader", "A"), celda("tableHeader", "B")]},
        {"type": "tableRow", "content": [celda("tableCell", "1"), celda("tableCell", "x|y")]},
        {"type": "tableRow", "content": [celda("tableCell", "solo una")]},
    ]})
    assert adf_a_markdown(documento) == (
        "| A | B |\n| --- | --- |\n| 1 | x\\|y |\n| solo una |  |"
    )


def test_bloque_de_codigo_con_y_sin_lenguaje() -> None:
    con = {"type": "codeBlock", "attrs": {"language": "python"},
           "content": [_texto("print(1)\nprint(2)")]}
    sin = {"type": "codeBlock", "content": [_texto("ls")]}
    assert adf_a_markdown(_doc(con, sin)) == "```python\nprint(1)\nprint(2)\n```\n\n```\nls\n```"


@pytest.mark.parametrize(
    ("tipo", "titulo"),
    [("info", "Info"), ("note", "Nota"), ("success", "Éxito"), ("warning", "Advertencia"),
     ("error", "Error"), ("custom", "Nota")],
)
def test_panel_como_cita_con_su_tipo(tipo: str, titulo: str) -> None:
    documento = _doc({"type": "panel", "attrs": {"panelType": tipo}, "content": [
        _parrafo(_texto("Ojo")), _parrafo(_texto("Segundo párrafo")),
    ]})
    assert adf_a_markdown(documento) == f"> **{titulo}:** Ojo\n>\n> Segundo párrafo"


def test_cita() -> None:
    assert adf_a_markdown(_doc({"type": "blockquote", "content": [_parrafo(_texto("dijo"))]})) == (
        "> dijo"
    )


def test_mencion_emoji_tarjeta_estado_y_fecha() -> None:
    documento = _doc(_parrafo(
        {"type": "mention", "attrs": {"id": "abc", "text": "@Ana"}},
        _texto(" "),
        {"type": "mention", "attrs": {"id": "def", "text": "Luis"}},
        _texto(" "),
        {"type": "emoji", "attrs": {"shortName": ":tada:", "text": "🎉"}},
        _texto(" "),
        {"type": "emoji", "attrs": {"shortName": ":ok:"}},
        _texto(" "),
        {"type": "inlineCard", "attrs": {"url": "https://ejemplo.com/x"}},
        _texto(" "),
        {"type": "status", "attrs": {"text": "EN CURSO", "color": "blue"}},
        _texto(" "),
        {"type": "date", "attrs": {"timestamp": "1767225600000"}},
    ))
    assert adf_a_markdown(documento) == (
        "@Ana @Luis 🎉 :ok: https://ejemplo.com/x [EN CURSO] 2026-01-01"
    )


def test_imagenes_y_adjuntos_se_avisan() -> None:
    documento = _doc(
        {"type": "mediaSingle", "content": [
            {"type": "media", "attrs": {"id": "a1b2", "type": "file", "alt": "captura.png"}},
        ]},
        {"type": "mediaGroup", "content": [
            {"type": "media", "attrs": {"id": "c3d4", "type": "file"}},
            {"type": "media", "attrs": {"id": "e5f6", "type": "file", "alt": "log.txt"}},
        ]},
        _parrafo(_texto("ver "), {"type": "mediaInline", "attrs": {"id": "g7"}}),
    )
    assert adf_a_markdown(documento) == (
        "[Imagen adjunta: captura.png, no incluida]\n\n"
        "[Imagen adjunta: c3d4, no incluida]\n[Imagen adjunta: log.txt, no incluida]\n\n"
        "ver [Imagen adjunta: g7, no incluida]"
    )


def test_desplegable_con_titulo() -> None:
    documento = _doc({"type": "expand", "attrs": {"title": "Detalles"},
                      "content": [_parrafo(_texto("oculto"))]})
    assert adf_a_markdown(documento) == "**Detalles**\n\noculto"


# --- Nodos desconocidos y ADF roto: nunca falla -------------------------------------------


def test_un_nodo_desconocido_deja_su_texto() -> None:
    documento = _doc(
        {"type": "nodoDelFuturo", "content": [_texto("a"), {"type": "otro", "content": [
            _texto("b")]}]},
        _parrafo(_texto("c"), {"type": "inlineDelFuturo", "content": [_texto("d")]}),
    )
    assert adf_a_markdown(documento) == "ab\n\ncd"


def test_un_nodo_desconocido_con_bloques_adentro_los_convierte() -> None:
    documento = _doc({"type": "layoutSection", "content": [
        {"type": "layoutColumn", "content": [_parrafo(_texto("izq"))]},
        {"type": "layoutColumn", "content": [{"type": "bulletList", "content": [
            _item(_parrafo(_texto("der")))]}]},
    ]})
    assert adf_a_markdown(documento) == "izq\n\n- der"


@pytest.mark.parametrize(
    "documento",
    [
        None,
        "texto",
        [],
        {},
        {"type": "doc"},
        {"type": "doc", "content": None},
        {"type": "doc", "content": [None, 3, "x", {"type": "paragraph"}]},
        {"type": "doc", "content": [{"type": "heading", "attrs": {"level": "dos"},
                                     "content": [{"type": "text"}]}]},
        {"type": "doc", "content": [{"type": "table", "content": [{"type": "tableRow"}]}]},
        {"type": "doc", "content": [{"type": "bulletList", "content": [None, {}]}]},
        {"type": "doc", "content": [{"type": "orderedList", "attrs": {"order": "a"},
                                     "content": [_item()]}]},
        {"type": "doc", "content": [{"type": "taskList", "content": [{"attrs": None}]}]},
        {"type": "doc", "content": [_parrafo({"type": "text", "text": "x", "marks": "mal"})]},
        {"type": "doc", "content": [_parrafo({"type": "date", "attrs": {"timestamp": "x"}})]},
    ],
)
def test_un_adf_roto_no_falla(documento: Any) -> None:
    assert isinstance(adf_a_markdown(documento), str)


def test_documento_vacio() -> None:
    assert adf_a_markdown(_doc()) == ""
    assert adf_a_markdown(None) == ""
