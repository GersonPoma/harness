import io
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from rich.console import Console

from harness import cli, consola
from harness.bucle import Paso
from harness.plan import ArchivoPlan, Plan
from harness.tipos import LlamadaHerramienta

RAIZ_PROYECTO = Path(__file__).resolve().parents[1]


def test_forzar_salida_utf8_reconfigura_stdout_y_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    salida = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    errores = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", salida)
    monkeypatch.setattr(sys, "stderr", errores)

    consola.forzar_salida_utf8()

    assert salida.encoding == errores.encoding == "utf-8"
    salida.write("→ ✓")
    salida.flush()
    assert salida.buffer.getvalue() == "→ ✓".encode()


def test_flujo_sin_reconfigure_se_deja_igual(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    consola.forzar_salida_utf8()


def test_salida_redirigida_con_pagina_de_codigos_cp1252() -> None:
    # Reproduce el caso real: un proceso aparte con la salida a una tubería en cp1252.
    codigo = (
        "from harness.consola import forzar_salida_utf8; forzar_salida_utf8(); print('→ ✓')"
    )
    proceso = subprocess.run(
        [sys.executable, "-c", codigo],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert proceso.returncode == 0, proceso.stderr.decode("utf-8", "replace")
    assert proceso.stdout.decode("utf-8").strip() == "→ ✓"


def test_el_comando_harness_arranca_por_main() -> None:
    # Si apuntara a 'app', forzar_salida_utf8 no se ejecutaría nunca en el comando real.
    pyproject = tomllib.loads((RAIZ_PROYECTO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["harness"] == "harness.cli:main"


def test_main_pasa_a_utf8_antes_de_arrancar(monkeypatch: pytest.MonkeyPatch) -> None:
    orden: list[str] = []
    monkeypatch.setattr(consola, "forzar_salida_utf8", lambda: orden.append("utf8"))
    monkeypatch.setattr(cli, "app", lambda: orden.append("app"))
    cli.main()
    assert orden == ["utf8", "app"]


def _mostrar_paso(monkeypatch: pytest.MonkeyPatch, argumentos: dict) -> str:
    grabadora = Console(record=True, width=200)
    monkeypatch.setattr(consola, "consola", grabadora)
    llamada = LlamadaHerramienta("l1", "leer_archivo", argumentos)
    consola.mostrar_paso(Paso(1, llamada, "ok", es_error=False))
    return grabadora.export_text().splitlines()[0]


def test_ruta_corta_se_muestra_entera(monkeypatch: pytest.MonkeyPatch) -> None:
    linea = _mostrar_paso(monkeypatch, {"ruta": "src/app.py"})
    assert linea == "[1] leer_archivo(ruta='src/app.py')"


def test_ruta_larga_se_abrevia_por_el_medio(monkeypatch: pytest.MonkeyPatch) -> None:
    ruta = "src/main/java/com/empresa/tienda/controladores/ProductoControlador.java"
    linea = _mostrar_paso(monkeypatch, {"ruta": ruta, "contenido": "x" * 500})

    abreviada = linea.split("'")[1]
    assert len(ruta) > consola.MAX_CARACTERES_RUTA
    assert len(abreviada) == consola.MAX_CARACTERES_RUTA
    inicio, final = abreviada.split(consola.ELIPSIS)
    assert ruta.startswith(inicio) and ruta.endswith(final)
    assert final.endswith("ProductoControlador.java")
    # El resto de los argumentos largos se sigue resumiendo.
    assert "contenido=<500 caracteres>" in linea


def _grabar(monkeypatch: pytest.MonkeyPatch) -> Console:
    grabadora = Console(record=True, width=200)
    monkeypatch.setattr(consola, "consola", grabadora)
    return grabadora


def test_un_paso_con_varias_llamadas_se_agrupa(monkeypatch: pytest.MonkeyPatch) -> None:
    grabadora = _grabar(monkeypatch)
    llamadas = [
        LlamadaHerramienta("a", "leer_archivo", {"ruta": "src/app.py"}),
        LlamadaHerramienta("b", "listar_directorio", {"ruta": "src"}),
        LlamadaHerramienta("c", "leer_archivo", {"ruta": "no.py"}),
    ]
    resultados = [("Archivo: src/app.py\nx = 1", False), ("app.py", False), ("Error: no", True)]
    for indice, (llamada, (resultado, es_error)) in enumerate(zip(llamadas, resultados), 1):
        consola.mostrar_paso(Paso(2, llamada, resultado, es_error, indice, total=3))

    assert grabadora.export_text().splitlines() == [
        "[2] 3 herramientas",
        "    · leer_archivo(ruta='src/app.py') → Archivo: src/app.py (+1 líneas)",
        "    · listar_directorio(ruta='src') → app.py",
        "    · leer_archivo(ruta='no.py') → Error: no",
    ]


def test_un_paso_con_una_llamada_mantiene_el_formato(monkeypatch: pytest.MonkeyPatch) -> None:
    grabadora = _grabar(monkeypatch)
    llamada = LlamadaHerramienta("a", "listar_directorio", {"ruta": "."})
    consola.mostrar_paso(Paso(1, llamada, "src/\nharness.yaml", es_error=False))
    assert grabadora.export_text().splitlines() == [
        "[1] listar_directorio(ruta='.')",
        "    → src/ (+1 líneas)",
    ]


# --- Nunca la última columna -------------------------------------------------------------

# El plan de TAREAS-5 con el que una terminal perdió el primer renglón del paso 2.
PLAN_TAREAS_5 = Plan(
    resumen=(
        "Implementar la eliminación de tareas mediante el endpoint DELETE /tareas/{id} y su "
        "soporte en el repositorio y tests."
    ),
    archivos=[
        ArchivoPlan(
            ruta="src/tareas/repositorio.py",
            accion="modificar",
            motivo="Agregar método eliminar(id) para borrar una tarea por su id y retornar "
            "booleano o indicador de éxito.",
        ),
        ArchivoPlan(
            ruta="tests/test_tareas.py",
            accion="modificar",
            motivo="Agregar tests para eliminación exitosa, caso de tarea inexistente y "
            "verificación de que las demás tareas no cambian de id ni desaparecen.",
        ),
    ],
    pasos=[
        "Implementar el método eliminar en RepositorioTareas en src/tareas/repositorio.py.",
        "Implementar el endpoint DELETE /tareas/{id} en src/tareas/main.py utilizando el "
        "método del repositorio y levantando HTTPException 404 si no se encuentra.",
    ],
    tests=["test_eliminar_tarea_existente"],
)


def _grabar_sin_ultima_columna(monkeypatch: pytest.MonkeyPatch, ancho: int) -> Console:
    grabadora = consola.ConsolaSinUltimaColumna(record=True, width=ancho)
    monkeypatch.setattr(consola, "consola", grabadora)
    return grabadora


def test_la_consola_del_harness_no_usa_la_ultima_columna() -> None:
    assert isinstance(consola.consola, consola.ConsolaSinUltimaColumna)


@pytest.mark.parametrize("ancho", [154, 100, 80, 60])
def test_ninguna_linea_del_plan_llega_a_la_ultima_columna(
    monkeypatch: pytest.MonkeyPatch, ancho: int
) -> None:
    grabadora = _grabar_sin_ultima_columna(monkeypatch, ancho)
    consola.mostrar_plan(PLAN_TAREAS_5)
    lineas = grabadora.export_text().splitlines()

    assert max(len(linea) for linea in lineas) == ancho - 1
    # Y no se pierde nada: el paso 2 está entero, aunque ocupe varios renglones.
    texto = " ".join(" ".join(l.strip("│| ").split()) for l in lineas)
    assert "2. Implementar el endpoint DELETE /tareas/{id}" in texto
    assert "levantando HTTPException 404 si no se encuentra." in texto


def test_un_aviso_largo_tampoco_llega_a_la_ultima_columna(monkeypatch: pytest.MonkeyPatch) -> None:
    grabadora = _grabar_sin_ultima_columna(monkeypatch, 80)
    consola.mostrar_aviso("palabra " * 60)
    assert max(len(linea) for linea in grabadora.export_text().splitlines()) <= 79


@pytest.mark.parametrize("legacy", [True, False])
def test_el_ancho_es_uno_menos_que_el_de_la_terminal(legacy: bool) -> None:
    assert consola.ConsolaSinUltimaColumna(width=100, legacy_windows=legacy).width == 99
