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
