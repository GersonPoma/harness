import json
from pathlib import Path

import pytest

from harness.ejecucion import ResultadoEjecucion, Validacion
from harness.registro import RegistroPasos
from harness.resumen_log import (
    formatear_duracion,
    formatear_tiempos,
    resumir_log,
    tiempos_por_etapa,
)

EVENTOS = [
    {"evento": "inicio", "t": 0.0, "proveedor": "gemini", "modelo": "flash", "perfil": "rapido",
     "agents_md": False, "revisor": "openrouter/x"},
    {"evento": "ticket", "t": 0.1, "ticket": "TAREAS-3", "titulo": "Completar tareas"},
    {"evento": "etapa", "t": 0.2, "etapa": "preparación"},
    {"evento": "etapa", "t": 14.2, "etapa": "plan"},
    {"evento": "herramienta", "t": 20.0, "paso": 1},
    {"evento": "etapa", "t": 52.0, "etapa": "implementación"},
    {"evento": "proveedor", "t": 60.0, "siguiente": "openrouter/nemotron"},
    {"evento": "etapa", "t": 100.0, "etapa": "validación"},
    {"evento": "validacion", "t": 109.0, "intento": 1, "es_valido": False},
    {"evento": "etapa", "t": 109.0, "etapa": "implementación"},
    {"evento": "etapa", "t": 130.0, "etapa": "validación"},
    {"evento": "validacion", "t": 139.0, "intento": 2, "es_valido": True,
     "resumen_tests": "8 passed in 0.18s"},
    {"evento": "etapa", "t": 139.0, "etapa": "revisión"},
    {"evento": "revision", "t": 160.0, "vuelta": 1, "es_aprobada": False, "es_fallida": False,
     "modelo": "openrouter/x",
     "problemas": [{"archivo": "src/main.py", "descripcion": "Nadie pidió el POST."}]},
    {"evento": "memoria", "t": 161.0},
    {"evento": "aviso", "t": 162.0, "mensaje": "Algo raro con ****."},
    {"evento": "fin", "t": 165.0, "pasos": 12, "tokens_entrada": 1000, "tokens_salida": 200,
     "es_validado": True, "validaciones": 2, "fuera_del_plan": ["notas.txt"]},
    {"evento": "etapa", "t": 165.0, "etapa": "entrega"},
    {"evento": "entrega", "t": 170.0, "rama": "feature/TAREAS-3", "commit": "abc1234def",
     "es_pusheado": True, "pull_request": "https://github.com/a/b/pull/7"},
    {"evento": "jira_transicion", "t": 171.0, "estado_pedido": "En revisión",
     "resultado": "movido"},
]


def test_tiempos_por_etapa_suma_las_que_se_repiten() -> None:
    assert tiempos_por_etapa(EVENTOS) == {
        "preparación": 14.0, "plan": 37.8, "implementación": 69.0, "validación": 18.0,
        "revisión": 26.0, "entrega": 6.0,
    }


def test_una_etapa_vacia_no_se_lista() -> None:
    eventos = [{"evento": "etapa", "t": 1.0, "etapa": "fuera del plan"},
               {"evento": "etapa", "t": 1.1, "etapa": "validación"},
               {"evento": "validacion", "t": 9.0}]
    assert tiempos_por_etapa(eventos) == {"validación": 7.9}


@pytest.mark.parametrize(
    ("segundos", "texto"), [(0.2, "menos de 1 s"), (8.4, "8 s"), (130, "2 min 10 s")]
)
def test_formatear_duracion(segundos: float, texto: str) -> None:
    assert formatear_duracion(segundos) == texto


def test_formatear_tiempos() -> None:
    assert formatear_tiempos({"plan": 38.0, "implementación": 130.0}) == (
        "plan 38 s · implementación 2 min 10 s"
    )


def test_el_resumen_cuenta_la_ejecucion() -> None:
    texto = resumir_log(EVENTOS)
    assert texto.startswith("# TAREAS-3: Completar tareas\n")
    assert "- **Resultado:** PR abierto: https://github.com/a/b/pull/7" in texto
    assert "- **Modelo:** gemini/flash, después openrouter/nemotron (perfil rapido)" in texto
    assert "- **Revisor:** openrouter/x" in texto
    assert "**Pasos:** 12 · **Tokens:** 1000 entrada / 200 salida · **Duración:** 2 min 51 s" in (
        texto
    )
    assert "| implementación | 1 min 9 s |" in texto
    assert "- 1: falló\n- 2: pasó (8 passed in 0.18s)" in texto
    assert "- Vuelta 1 (openrouter/x): 1 problema\n  - `src/main.py`: Nadie pidió el POST." in (
        texto
    )
    assert "- Sin justificar `notas.txt`" in texto
    assert "- Commit `abc1234` en `feature/TAREAS-3`" in texto
    assert "- Ticket a 'En revisión': movido" in texto
    assert "- **Memoria:** 1 notas nuevas" in texto
    assert "- Algo raro con ****." in texto
    assert "El repo no tiene AGENTS.md" in texto


def test_un_corte_es_el_resultado() -> None:
    eventos = [*EVENTOS[:8], {"evento": "error", "t": 101.0, "mensaje": "Gemini no responde."}]
    assert "- **Resultado:** se cortó: Gemini no responde." in resumir_log(eventos)


def test_el_registro_mide_el_tiempo_y_escribe_el_resumen_sin_secretos(repo: Path) -> None:
    horas = iter([100.0, 100.0, 101.5, 112.0, 120.0, 125.0])
    registro = RegistroPasos(repo, "T-1", secretos=["sk-secreta-123"], reloj=lambda: next(horas))
    registro.registrar_inicio("gemini", "flash")
    registro.registrar_etapa("implementación")
    registro.registrar_validacion(Validacion(1, True, "sk-secreta-123", "3 passed"))
    registro.registrar_fin(ResultadoEjecucion("Listo sk-secreta-123", 3, 10, 5, False, True, 1))
    eventos = [json.loads(linea) for linea in registro.ruta.read_text("utf-8").splitlines()]
    assert [e["t"] for e in eventos] == [0.0, 1.5, 12.0, 25.0]
    assert eventos[-1]["tiempos"] == {"implementación": 18.5}
    ruta = registro.escribir_resumen()
    assert ruta == registro.ruta.with_suffix(".md")
    resumen = ruta.read_text(encoding="utf-8")
    assert "sk-secreta-123" not in resumen and "- 1: pasó (3 passed)" in resumen
