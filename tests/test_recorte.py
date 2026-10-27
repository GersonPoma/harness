from pathlib import Path

import pytest

from harness.herramientas.recorte import MAX_CARACTERES_LINEA, recortar_salida

# Salida real de "mvn -B -q test" en tienda-api (TIENDA-3): logs INFO de Spring, avisos de
# Mockito y de la JVM, y un test que falla con "expected: 10 / but was: 9".
SALIDA_MAVEN = (
    (Path(__file__).parent / "datos" / "salida_maven_con_ruido.txt")
    .read_text(encoding="utf-8")
    .strip()
)
ERROR_JUNIT = "org.opentest4j.AssertionFailedError"
UBICACION = "VentaControllerTest.java:188"
RESUMEN = "[ERROR] Tests run: 27, Failures: 1, Errors: 0, Skipped: 0"


def _con_ruido_despues_del_error(salida: str, repeticiones: int) -> str:
    """Como si otras clases de test siguieran escribiendo logs después de la que falló."""
    ruido = [linea for linea in salida.splitlines() if " INFO " in linea]
    inicio_resumen = salida.index("[ERROR] Failures:")
    return salida[:inicio_resumen] + "\n".join(ruido * repeticiones) + "\n" + salida[
        inicio_resumen:
    ]


def test_salida_corta_no_se_toca() -> None:
    assert recortar_salida(SALIDA_MAVEN, len(SALIDA_MAVEN)) == SALIDA_MAVEN


def test_maven_real_conserva_el_error_que_el_final_solo_perdia() -> None:
    tope = 1200
    recortada = recortar_salida(SALIDA_MAVEN, tope)

    # Con solo el final, el tipo de error y la línea del test quedaban afuera.
    assert ERROR_JUNIT not in SALIDA_MAVEN[-tope:]
    assert ERROR_JUNIT in recortada and UBICACION in recortada
    assert "expected: 10\n but was: 9" in recortada
    assert RESUMEN in recortada
    assert "líneas omitidas ...]" in recortada
    assert len(recortada) <= tope


def test_maven_con_ruido_despues_del_error() -> None:
    salida = _con_ruido_despues_del_error(SALIDA_MAVEN, repeticiones=10)
    tope = 6000
    recortada = recortar_salida(salida, tope)

    assert ERROR_JUNIT not in salida[-tope:]
    assert ERROR_JUNIT in recortada and UBICACION in recortada
    assert RESUMEN in recortada
    assert len(recortada) <= tope


def test_el_texto_fijo_de_maven_no_ocupa_lugar() -> None:
    recortada = recortar_salida(SALIDA_MAVEN, 2000)
    assert "Re-run Maven using the -X switch" not in recortada
    assert "MojoFailureException" not in recortada
    assert recortada.endswith("líneas omitidas ...]")


def test_lo_omitido_se_marca_con_la_cantidad_de_lineas() -> None:
    lineas = [f"INFO linea {numero}" for numero in range(100)]
    lineas[10] = "ERROR: falló la conexión"
    recortada = recortar_salida("\n".join(lineas), 400)

    assert recortada.startswith("[... 8 líneas omitidas ...]\nINFO linea 8\nINFO linea 9\n")
    assert "ERROR: falló la conexión\nINFO linea 11\nINFO linea 12\nINFO linea 13\n" in recortada
    assert recortada.endswith("INFO linea 99")
    assert recortada.count("líneas omitidas") == 2


def test_pytest() -> None:
    ruido = [f"tests/test_x.py::test_{numero} PASSED" for numero in range(300)]
    falla = [
        "    def test_suma():",
        ">       assert suma(2, 2) == 5",
        "E       assert 4 == 5",
        "tests/test_x.py:12: AssertionError",
    ]
    final = ["=========== 1 failed, 300 passed in 2.01s ==========="]
    salida = "\n".join(ruido[:100] + falla + ruido[100:] + final)
    recortada = recortar_salida(salida, 1500)

    assert "E       assert 4 == 5" in recortada
    assert "tests/test_x.py:12: AssertionError" in recortada
    assert recortada.endswith(final[0])


def test_sin_errores_conserva_el_final() -> None:
    salida = "\n".join(f"INFO linea {numero}" for numero in range(500))
    recortada = recortar_salida(salida, 300)
    assert recortada.startswith("[... ")
    assert recortada.endswith("INFO linea 499")
    assert len(recortada) <= 300


def test_una_linea_enorme_se_acorta() -> None:
    enorme = "x" * 5000
    salida = "\n".join(["ERROR al guardar", enorme, "fin"])
    recortada = recortar_salida(salida, 2000)
    assert "ERROR al guardar" in recortada
    assert "x" * MAX_CARACTERES_LINEA + " [... línea recortada]" in recortada
    assert recortada.endswith("fin")


@pytest.mark.parametrize("tope", [50, 200, 1000, 4000, 6000])
def test_nunca_pasa_del_tope(tope: int) -> None:
    salida = _con_ruido_despues_del_error(SALIDA_MAVEN, repeticiones=5)
    assert len(recortar_salida(salida, tope)) <= tope
