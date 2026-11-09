import pytest

from harness.ejecucion import ejecutar_ticket
from harness.resumen_tests import resumir_tests, seccion_tests
from harness.tipos import Mensaje, Rol
from tests.falsos import ProveedorFalso, terminar

# Salidas reales de las validaciones de los proyectos de prueba (logs de las fases 2 a 9).
PYTEST = """Código de salida: 0
..                                                                       [100%]
2 passed in 0.36s"""
GRADLE = """Código de salida: 0
> Task :test

OpenJDK 64-Bit Server VM warning: Sharing is only supported for boot loader classes
2026-10-27T22:17:46.630Z  INFO 1212 --- [library-api] HikariPool-1 - Shutdown completed.

BUILD SUCCESSFUL in 1m 1s
5 actionable tasks: 5 executed"""
# mvn -q no imprime el resumen: solo un aviso de la JVM, que no dice nada de los tests.
MAVEN_Q = """Código de salida: 0
OpenJDK 64-Bit Server VM warning: Sharing is only supported for boot loader classes"""
MAVEN = """[INFO] Tests run: 3, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.2 s
[INFO] Results:
[INFO] Tests run: 12, Failures: 0, Errors: 0, Skipped: 1
[INFO] BUILD SUCCESS"""


@pytest.mark.parametrize(
    ("salida", "resumen"),
    [
        (PYTEST, "2 passed in 0.36s"),
        ("===== 5 passed, 1 warning in 0.80s =====", "5 passed, 1 warning in 0.80s"),
        (MAVEN, "Tests run: 12, Failures: 0, Errors: 0, Skipped: 1"),
        (GRADLE, "BUILD SUCCESSFUL in 1m 1s"),
        ("> Task :test\n12 tests completed, 0 failed", "12 tests completed, 0 failed"),
        ("Tests:       3 passed, 3 total\nTime: 1.2 s", "Tests:       3 passed, 3 total"),
        (MAVEN_Q, None),
        ("\x1b[32m4 passed\x1b[0m in 0.1s", "4 passed in 0.1s"),
        ("Código de salida: 0\n==========\nTodo OK\n-----", "Todo OK"),
        ("", None),
    ],
)
def test_resumir_tests(salida: str, resumen: str | None) -> None:
    assert resumir_tests(salida) == resumen


def test_un_resumen_largo_se_recorta() -> None:
    resumen = resumir_tests("x" * 300)
    assert len(resumen) == 120 and resumen.endswith("…")


def test_la_seccion_de_tests_no_incluye_el_lint() -> None:
    informe = "## Tests (pytest -q)\nCódigo de salida: 0\n3 passed\n\n## Lint (ruff)\n1 error"
    assert seccion_tests(informe) == "Código de salida: 0\n3 passed\n"


def test_la_ejecucion_guarda_el_resumen_de_la_validacion_que_paso() -> None:
    informes = iter([
        (False, "## Tests (pytest)\nCódigo de salida: 1\n1 failed, 1 passed in 0.2s"),
        (True, "## Tests (pytest)\nCódigo de salida: 0\n2 passed in 0.1s"),
    ])
    validaciones = []
    resultado = ejecutar_ticket(
        ProveedorFalso([terminar(), terminar()]), [Mensaje(Rol.USUARIO, "T")], [], 10,
        validar=lambda: next(informes), max_intentos_validacion=2,
        al_validar=validaciones.append,
    )
    assert resultado.resumen_tests == "2 passed in 0.1s"
    assert [v.resumen_tests for v in validaciones] == [None, "2 passed in 0.1s"]
