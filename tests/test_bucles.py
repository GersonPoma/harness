"""Detección de bucles: el mismo contenido en el mismo archivo, o el mismo error de tests."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness.bucle import ErrorBucle, Paso, ejecutar_bucle
from harness.bucles import DetectorBucles, normalizar_salida
from harness.config import ConfigModelo
from harness.ejecucion import ejecutar_ticket
from harness.herramientas import crear_herramientas
from harness.sesion import MOTIVO_BUCLE, cargar_sesion
from harness.tipos import LlamadaHerramienta, Mensaje, Rol
from tests.falsos import ProveedorFalso, pedir, terminar


def _escritura(repo: Path, contenido: str, numero: int = 1) -> Paso:
    # Como queda después de escribir_archivo: el detector mira el archivo en el disco.
    (repo / "servicio.py").write_text(contenido, encoding="utf-8")
    llamada = LlamadaHerramienta(f"e{numero}", "escribir_archivo", {"ruta": "servicio.py"})
    return Paso(numero, llamada, "Archivo 'servicio.py' escrito.", es_error=False)


def _tests(salida: str, numero: int = 1) -> Paso:
    llamada = LlamadaHerramienta(f"t{numero}", "ejecutar_tests", {})
    return Paso(numero, llamada, salida, es_error=False)


FALLA = "Código de salida: 1\nFAILED test_total - assert 9 == 10\n1 failed in 0.31s"


def test_la_tercera_escritura_igual_lleva_una_nota(tmp_path: Path) -> None:
    detector = DetectorBucles(tmp_path)
    assert detector.revisar(_escritura(tmp_path, "v1")) is None
    assert detector.revisar(_escritura(tmp_path, "v1")) is None
    nota = detector.revisar(_escritura(tmp_path, "v1"))
    assert nota.startswith("Aviso del harness: es la 3.ª vez que dejas 'servicio.py' con")
    assert "replantea el enfoque" in nota


def test_volver_a_un_contenido_anterior_tambien_cuenta(tmp_path: Path) -> None:
    # A -> B -> A -> B -> A: el archivo vuelve 3 veces al mismo contenido.
    detector = DetectorBucles(tmp_path)
    notas = [detector.revisar(_escritura(tmp_path, texto)) for texto in "ABABA"]
    assert notas[:4] == [None, None, None, None] and notas[4] is not None


def test_la_quinta_repeticion_corta(tmp_path: Path) -> None:
    detector = DetectorBucles(tmp_path)
    for _ in range(4):
        detector.revisar(_escritura(tmp_path, "igual"))
    with pytest.raises(ErrorBucle, match="5 veces seguidas"):
        detector.revisar(_escritura(tmp_path, "igual"))


def test_el_mismo_error_de_tests_seguido(tmp_path: Path) -> None:
    detector = DetectorBucles(tmp_path)
    # Solo cambia la duración: es el mismo error.
    salidas = [FALLA, FALLA.replace("0.31s", "0.29s"), FALLA.replace("0.31s", "1.02s")]
    notas = [detector.revisar(_tests(salida)) for salida in salidas]
    assert notas[:2] == [None, None]
    assert "los tests fallan con exactamente el mismo error" in notas[2]


def test_un_error_distinto_o_un_paso_que_pasa_reinicia_la_cuenta(tmp_path: Path) -> None:
    detector = DetectorBucles(tmp_path)
    secuencia = [FALLA, FALLA, "Código de salida: 0\n1 passed", FALLA, FALLA,
                 FALLA.replace("9 == 10", "8 == 10")]
    assert all(detector.revisar(_tests(salida)) is None for salida in secuencia)


def test_un_paso_con_error_no_cuenta(tmp_path: Path) -> None:
    detector = DetectorBucles(tmp_path)
    llamada = LlamadaHerramienta("e1", "escribir_archivo", {"ruta": "harness.yaml"})
    paso = Paso(1, llamada, "Error: protegido", es_error=True)
    assert all(detector.revisar(paso) is None for _ in range(6))


def test_normalizar_salida() -> None:
    texto = "2026-11-09T12:00:01.123Z error en 0x7f3a2b en 3.2 s (12ms)"
    assert normalizar_salida(texto) == "<x> error en <x> en <x> (<x>)"


def test_el_bucle_agrega_la_nota_y_corta_despues_de_guardar_los_resultados(
    tmp_path: Path,
) -> None:
    escribir = LlamadaHerramienta("e", "escribir_archivo", {"ruta": "a.py", "contenido": "x"})
    proveedor = ProveedorFalso([pedir(escribir)] * 5)
    mensajes = [Mensaje(Rol.USUARIO, "T")]
    with pytest.raises(ErrorBucle):
        ejecutar_bucle(
            proveedor, mensajes, crear_herramientas(tmp_path), 10,
            revisar_paso=DetectorBucles(tmp_path).revisar,
        )
    resultados = [m.contenido for m in mensajes if m.rol == Rol.HERRAMIENTA]
    # Los 5 resultados quedaron guardados (sin llamadas sin respuesta para la sesión).
    assert len(resultados) == 5
    assert "Aviso del harness: es la 3.ª vez" in resultados[2]
    assert "Aviso del harness" not in resultados[1]


def test_la_misma_validacion_fallida_se_le_dice_al_modelo() -> None:
    informes = iter([(False, FALLA), (False, FALLA.replace("0.31s", "0.4s")), (True, "ok")])
    proveedor = ProveedorFalso([terminar(), terminar(), terminar()])
    ejecutar_ticket(
        proveedor, [Mensaje(Rol.USUARIO, "T")], [], 10, validar=lambda: next(informes),
        max_intentos_validacion=3,
    )
    primera, segunda = (c[-1].contenido for c in proveedor.conversaciones[1:])
    assert "mismo error que en la validación anterior" not in primera
    assert "Es exactamente el mismo error que en la validación anterior" in segunda


def test_un_bucle_corta_la_ejecucion_y_se_puede_retomar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "T-1.md").write_text("# T-1: algo\n", encoding="utf-8")
    escribir = LlamadaHerramienta("e", "escribir_archivo", {"ruta": "a.py", "contenido": "x"})
    config = ConfigModelo("gemini", "modelo-x", None, "clave-falsa-0123456789")
    monkeypatch.setattr(cli, "cargar_config", lambda **_: config)
    monkeypatch.setattr(cli, "crear_proveedor", lambda *_: ProveedorFalso([pedir(escribir)] * 5))
    argumentos = ["run", str(tmp_path / "T-1.md"), "--repo", str(repo), "--sin-plan"]
    resultado = CliRunner().invoke(cli.app, argumentos)
    salida = " ".join(resultado.output.split())
    assert resultado.exit_code == 1
    assert "El modelo está en un bucle: 5 veces seguidas" in salida
    assert "harness resume T-1" in salida
    assert cargar_sesion(repo, "T-1").motivo_corte == MOTIVO_BUCLE
