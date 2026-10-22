import io
import os
import subprocess
import sys

import pytest

from harness import consola


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
