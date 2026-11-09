import io
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from rich.console import Console

from harness import cli, consola
from harness.bucle import Paso
from harness.cache_dependencias import EstadoCache
from harness.entorno_docker import Instalacion
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
    monkeypatch.setattr(cli, "cargar_env", lambda: orden.append("env"))
    monkeypatch.setattr(cli, "app", lambda: orden.append("app"))
    cli.main()
    assert orden == ["utf8", "env", "app"]


# --- Salida cruda (HARNESS_CONSOLA_CRUDA) ------------------------------------------------


def test_la_salida_cruda_es_lo_mismo_que_va_a_la_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    salida = _terminal_en_texto(monkeypatch)
    ruta = tmp_path / "cruda.txt"
    consola.grabar_salida_cruda(ruta)
    with consola.estado("Instalando dependencias..."):
        consola.mostrar_aviso("un aviso")
    consola.consola.print("[bold]fin[/]")
    consola.consola.file.flush()

    # Byte a byte, con los códigos ANSI y sin convertir los saltos de línea.
    cruda = ruta.read_bytes().decode("utf-8")
    assert cruda == salida.getvalue()
    assert "\x1b[2K" in cruda and "\r\n" not in cruda
    assert _pantalla(cruda) == ["Instalando dependencias...", "un aviso", "fin"]


class _TerminalFalsa(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 1


@pytest.mark.parametrize("es_terminal", [True, False])
def test_la_copia_no_cambia_como_dibuja_rich(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, es_terminal: bool
) -> None:
    # Rich decide por isatty y fileno si dibuja para una terminal: se los pregunta a la real.
    terminal = _TerminalFalsa() if es_terminal else io.StringIO()
    monkeypatch.setattr(consola, "consola", consola.ConsolaSinUltimaColumna(file=terminal))
    consola.grabar_salida_cruda(tmp_path / "cruda.txt")
    assert consola.consola.is_terminal is es_terminal
    if es_terminal:
        assert consola.consola.file.fileno() == 1


def test_main_activa_la_salida_cruda(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ruta = tmp_path / "cruda.txt"
    monkeypatch.setenv("HARNESS_CONSOLA_CRUDA", str(ruta))
    monkeypatch.setattr(consola, "forzar_salida_utf8", lambda: None)
    monkeypatch.setattr(cli, "cargar_env", lambda: None)
    recibidas: list[Path] = []
    monkeypatch.setattr(consola, "grabar_salida_cruda", recibidas.append)
    monkeypatch.setattr(cli, "app", lambda: None)
    cli.main()
    assert recibidas == [ruta]


def test_main_sin_la_variable_no_copia_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consola, "forzar_salida_utf8", lambda: None)
    monkeypatch.setattr(cli, "cargar_env", lambda: None)
    monkeypatch.setattr(consola, "grabar_salida_cruda", lambda _: pytest.fail("no debía copiar"))
    monkeypatch.setattr(cli, "app", lambda: None)
    cli.main()


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


# --- Indicador animado --------------------------------------------------------------------


def _terminal_grabada(monkeypatch: pytest.MonkeyPatch) -> Console:
    grabadora = consola.ConsolaSinUltimaColumna(record=True, width=80, force_terminal=True)
    monkeypatch.setattr(consola, "consola", grabadora)
    return grabadora


def test_el_indicador_deja_su_linea_y_no_borra_lo_que_sigue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grabadora = _terminal_grabada(monkeypatch)
    with consola.estado("Leyendo TAREAS-1 de Jira..."):
        pass
    grabadora.print("# TAREAS-1: título\n\n**Tipo:** Tarea\n**Prioridad:** Medium")

    # Uno transitorio habría borrado su línea al cerrarse (subiendo el cursor): este la deja.
    assert grabadora.export_text().splitlines() == [
        "Leyendo TAREAS-1 de Jira...",
        "# TAREAS-1: título",
        "",
        "**Tipo:** Tarea",
        "**Prioridad:** Medium",
    ]


def test_el_indicador_queda_con_su_ultimo_mensaje(monkeypatch: pytest.MonkeyPatch) -> None:
    grabadora = _terminal_grabada(monkeypatch)
    with consola.estado("Preparando Docker...") as indicador:
        consola.actualizar_estado(indicador, "Instalando dependencias [1/2]...")
    assert grabadora.export_text().splitlines() == ["Instalando dependencias [1/2]..."]


def _terminal_en_texto(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    salida = io.StringIO()
    terminal = consola.ConsolaSinUltimaColumna(
        file=salida, width=80, force_terminal=True, color_system=None
    )
    monkeypatch.setattr(consola, "consola", terminal)
    return salida


def _pantalla(salida: str) -> list[str]:
    """Lo que queda en una terminal: interpreta \\r, \\n, borrar línea y subir el cursor."""
    lineas, fila, columna = [""], 0, 0
    for parte in re.split(r"(\x1b\[[0-9;?]*[A-Za-z]|\r|\n)", salida):
        if parte == "\n":
            fila, columna = fila + 1, 0
            if fila == len(lineas):
                lineas.append("")
        elif parte == "\r":
            columna = 0
        elif parte == "\x1b[2K":
            lineas[fila] = ""
        elif re.fullmatch(r"\x1b\[\d*A", parte):
            fila -= int(parte[2:-1] or 1)
        elif parte and not parte.startswith("\x1b"):
            actual = lineas[fila].ljust(columna)
            lineas[fila] = actual[:columna] + parte + actual[columna + len(parte):]
            columna += len(parte)
    return [linea for linea in lineas if linea]


def test_lo_que_se_imprime_durante_el_indicador_sale_despues_de_su_mensaje(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    salida = _terminal_en_texto(monkeypatch)
    with consola.estado("Preparando Docker...") as indicador:
        consola.actualizar_estado(indicador, "Esperando a que el servicio db esté listo...")
        consola.mostrar_cache(EstadoCache("harness-cache-tareas"))
        consola.actualizar_estado(indicador, "Instalando dependencias...")
        consola.mostrar_instalacion(Instalacion(8.2, True))
    assert _pantalla(salida.getvalue()) == [
        "Esperando a que el servicio db esté listo...",
        "Caché de dependencias: volumen harness-cache-tareas (reutilizado).",
        "Instalando dependencias...",
        "Dependencias instaladas en 8 s.",
    ]


def test_un_aviso_fija_el_indicador_una_sola_vez(monkeypatch: pytest.MonkeyPatch) -> None:
    salida = _terminal_en_texto(monkeypatch)
    with consola.estado("Conectando con el servidor MCP github..."):
        consola.mostrar_aviso("primer aviso")
        consola.mostrar_aviso("segundo aviso")
    consola.mostrar_aviso("fuera del indicador")
    assert _pantalla(salida.getvalue()) == [
        "Conectando con el servidor MCP github...",
        "primer aviso",
        "segundo aviso",
        "fuera del indicador",
    ]


def test_sin_nada_impreso_el_indicador_queda_como_antes(monkeypatch: pytest.MonkeyPatch) -> None:
    salida = _terminal_en_texto(monkeypatch)
    with consola.estado("Preparando Docker...") as indicador:
        consola.actualizar_estado(indicador, "Instalando dependencias...")
    consola.mostrar_aviso("después")
    assert _pantalla(salida.getvalue()) == ["Instalando dependencias...", "después"]


def test_fuera_de_una_terminal_el_indicador_no_dibuja_nada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grabadora = consola.ConsolaSinUltimaColumna(record=True, width=80)
    monkeypatch.setattr(consola, "consola", grabadora)
    with consola.estado("Conectando con el servidor MCP github..."):
        pass
    assert grabadora.export_text() == ""


def test_al_cerrar_no_sube_el_cursor_ni_borra_lineas(monkeypatch: pytest.MonkeyPatch) -> None:
    def cerrar(con_rich: bool) -> str:
        salida = io.StringIO()
        terminal = consola.ConsolaSinUltimaColumna(
            file=salida, width=80, force_terminal=True, color_system=None
        )
        monkeypatch.setattr(consola, "consola", terminal)
        with terminal.status("Leyendo...") if con_rich else consola.estado("Leyendo..."):
            pass
        terminal.print("línea siguiente")
        return salida.getvalue()

    # \x1b[2K borra una línea y \x1b[nA sube el cursor: con eso el transitorio de Rich borraba.
    assert "\x1b[2K" in cerrar(con_rich=True)
    nuestro = cerrar(con_rich=False)
    assert "\x1b[2K" not in nuestro and not re.search(r"\x1b\[\d*A", nuestro)
    assert nuestro.endswith("Leyendo...\n\x1b[?25hlínea siguiente\n")


# --- Caché de dependencias ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("segundos", "texto"),
    [(0.3, "menos de 1 s"), (8.2, "8 s"), (59.6, "1 min 0 s"), (112.4, "1 min 52 s")],
)
def test_formatear_duracion(segundos: float, texto: str) -> None:
    assert consola.formatear_duracion(segundos) == texto


@pytest.mark.parametrize(
    ("cache", "texto"),
    [
        (
            EstadoCache("harness-cache-tienda-api", es_creado=True),
            "Caché de dependencias: volumen harness-cache-tienda-api (nuevo: esta vez se "
            "descarga todo).",
        ),
        (
            EstadoCache("harness-cache-tienda-api", variables_respetadas=("PIP_CACHE_DIR",)),
            "Caché de dependencias: volumen harness-cache-tienda-api (reutilizado). Se respetan "
            "de harness.yaml: PIP_CACHE_DIR.",
        ),
        (EstadoCache(motivo="--sin-cache"), "Sin caché de dependencias (--sin-cache)."),
        (
            EstadoCache(motivo="docker.cache: false en harness.yaml"),
            "Sin caché de dependencias (docker.cache: false en harness.yaml).",
        ),
        (
            EstadoCache(motivo="otra ejecución de tienda-api la está usando (harness-x)"),
            "Esta ejecución corre sin caché de dependencias: otra ejecución de tienda-api la "
            "está usando (harness-x).",
        ),
    ],
)
def test_mostrar_cache(monkeypatch: pytest.MonkeyPatch, cache: EstadoCache, texto: str) -> None:
    grabadora = _grabar(monkeypatch)
    consola.mostrar_cache(cache)
    assert grabadora.export_text().strip() == texto


def test_mostrar_instalacion(monkeypatch: pytest.MonkeyPatch) -> None:
    grabadora = _grabar(monkeypatch)
    consola.mostrar_instalacion(Instalacion(112.4, es_ok=True))
    consola.mostrar_instalacion(Instalacion(8.0, es_ok=True, archivo_cambiado="pom.xml"))
    # Si falló, el error ya lo dice: no se muestra el tiempo.
    consola.mostrar_instalacion(Instalacion(3.0, es_ok=False))
    assert grabadora.export_text().splitlines() == [
        "Dependencias instaladas en 1 min 52 s.",
        "Dependencias reinstaladas en 8 s (cambió pom.xml).",
    ]
