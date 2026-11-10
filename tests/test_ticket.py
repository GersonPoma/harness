from pathlib import Path

import pytest

from harness.config import ErrorConfiguracion, leer_config_jira
from harness.ticket import (
    FUENTE_JIRA,
    FUENTE_LOCAL,
    MAX_CARACTERES_DESCRIPCION,
    Ticket,
    crear_ticket_local,
    limitar_descripcion,
)


def test_un_ticket_local_llega_tal_cual() -> None:
    texto = "# T-1: Crear tareas\n\n**Tipo:** Historia\n\nDescripción."
    ticket = Ticket("T-1", "Crear tareas", texto, FUENTE_LOCAL)
    assert ticket.a_markdown() == texto


def test_un_ticket_local_con_bom(tmp_path: Path) -> None:
    ruta = tmp_path / "MEM-1.md"
    ruta.write_text("# MEM-1: Nota en la memoria\n\nTexto.", encoding="utf-8-sig")
    ticket = crear_ticket_local(ruta)
    assert ticket.titulo == "Nota en la memoria"
    assert ticket.a_markdown().startswith("# MEM-1")


def test_un_ticket_de_jira_con_el_formato_de_los_md_locales() -> None:
    ticket = Ticket(
        "TAREAS-2", "Crear tareas", "## Descripción\n\nTexto.", FUENTE_JIRA,
        tipo="Historia", prioridad="Media", estado="Por hacer",
        url="https://ejemplo.atlassian.net/browse/TAREAS-2",
    )
    assert ticket.a_markdown() == (
        "# TAREAS-2: Crear tareas\n\n**Tipo:** Historia\n**Prioridad:** Media\n"
        "**Estado:** Por hacer\n\n## Descripción\n\nTexto."
    )
    # La URL no le sirve al modelo.
    assert "atlassian.net" not in ticket.a_markdown()


def test_solo_los_datos_que_hay() -> None:
    ticket = Ticket("T-1", "Título", "Texto.", FUENTE_JIRA, prioridad="Alta")
    assert ticket.a_markdown() == "# T-1: Título\n\n**Prioridad:** Alta\n\nTexto."


def test_limitar_descripcion() -> None:
    corta = "a" * MAX_CARACTERES_DESCRIPCION
    assert limitar_descripcion(corta) == (corta, None)

    larga = "b" * (MAX_CARACTERES_DESCRIPCION + 1)
    texto, originales = limitar_descripcion(larga)
    assert originales == MAX_CARACTERES_DESCRIPCION + 1
    assert texto == "b" * MAX_CARACTERES_DESCRIPCION + (
        f"\n\n[... se omitieron 1 de {originales} caracteres de la descripción ...]"
    )


# --- Configuración de Jira ----------------------------------------------------------------


def test_leer_config_jira(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_URL", "https://ejemplo.atlassian.net/")
    monkeypatch.setenv("JIRA_EMAIL", "yo@ejemplo.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    config = leer_config_jira()
    # Sin la barra final: las rutas se arman con "/rest/api/3/...".
    assert config.url == "https://ejemplo.atlassian.net"
    assert (config.email, config.token) == ("yo@ejemplo.com", "tok")


def test_leer_config_jira_dice_que_falta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_URL", "https://ejemplo.atlassian.net")
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.setenv("JIRA_API_TOKEN", " ")
    with pytest.raises(ErrorConfiguracion, match="falta JIRA_EMAIL, JIRA_API_TOKEN: guárdalo"):
        leer_config_jira()
