from pathlib import Path

import pytest

from harness import contexto as modulo_contexto
from harness.contexto import ContextoProyecto, cargar_agents_md
from harness.prompts import crear_mensajes_iniciales
from harness.tipos import Rol


def test_carga_agents_md(repo: Path) -> None:
    texto = "# demo\nCapas: rutas -> repositorio\n"
    (repo / "AGENTS.md").write_text(texto, encoding="utf-8")
    contexto = cargar_agents_md(repo)
    assert contexto.existe and not contexto.es_recortado
    assert contexto.agents_md == texto
    assert contexto.caracteres_originales == len(texto)


def test_sin_agents_md(repo: Path) -> None:
    assert cargar_agents_md(repo) == ContextoProyecto()
    assert not cargar_agents_md(repo).existe


def test_quita_la_bom(repo: Path) -> None:
    (repo / "AGENTS.md").write_bytes("﻿# demo".encode())
    assert cargar_agents_md(repo).agents_md == "# demo"


def test_agents_md_largo_se_recorta(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo_contexto, "MAX_CARACTERES_AGENTS", 10)
    (repo / "AGENTS.md").write_text("x" * 25, encoding="utf-8")
    contexto = cargar_agents_md(repo)
    assert contexto.agents_md == "x" * 10
    assert contexto.caracteres_originales == 25


def test_el_system_prompt_incluye_agents_md() -> None:
    contexto = ContextoProyecto("# tareas-api\nUsar RepositorioTareas.", 37)
    mensajes = crear_mensajes_iniciales("TICKET", contexto)
    sistema = mensajes[0].contenido
    assert mensajes[0].rol == Rol.SISTEMA
    assert "ya lo tienes, no lo vuelvas a leer" in sistema
    assert sistema.endswith("# tareas-api\nUsar RepositorioTareas.")
    assert "léelo con leer_archivo" not in sistema
    assert mensajes[1].contenido.endswith("TICKET")


def test_el_system_prompt_avisa_si_agents_md_se_recorto() -> None:
    contexto = ContextoProyecto("x" * 12_000, 30_000)
    sistema = crear_mensajes_iniciales("T", contexto)[0].contenido
    assert "se muestran 12000 de 30000 caracteres" in sistema


def test_el_system_prompt_avisa_si_no_hay_agents_md() -> None:
    sistema = crear_mensajes_iniciales("T", ContextoProyecto())[0].contenido
    assert "el repo no tiene AGENTS.md" in sistema
    assert "no lo vuelvas a leer" not in sistema
