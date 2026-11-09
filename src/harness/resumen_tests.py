"""La línea que dice cuántos tests pasaron, sacada de la salida del comando de tests.

Va en la descripción del PR ("5 passed in 0.80s"): la validación solo sabía que pasaron.
"""

import re

from harness.herramientas.comandos import PREFIJO_CODIGO_SALIDA, PREFIJO_TIEMPO_AGOTADO

MAX_CARACTERES_RESUMEN = 120
ENCABEZADO_TESTS = "## Tests"
ENCABEZADO_SECCION = "\n## "
PATRON_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Del más específico al más general; de cada uno vale la última aparición (el total).
PATRONES_RESUMEN = (
    # Maven (surefire): "Tests run: 12, Failures: 0, Errors: 0, Skipped: 0".
    re.compile(r"Tests run: \d+, Failures: \d+, Errors: \d+, Skipped: \d+"),
    # Gradle con testLogging: "12 tests completed, 0 failed".
    re.compile(r"\b\d+ tests? completed[^\n]*"),
    # Jest: "Tests:       3 passed, 3 total".
    re.compile(r"Tests:\s+\d+[^\n]*"),
    # pytest: "5 passed, 1 warning in 0.80s", al principio de la línea (el grupo, sin los "=").
    re.compile(r"(?m)^[=\s]*(\d+ (?:passed|failed)\b[^\n=]*)"),
    # Gradle sin testLogging no dice cuántos: al menos que el build terminó bien.
    re.compile(r"BUILD SUCCESSFUL in [^\n]+"),
)
# Decoración que no aporta en la última línea: "=====", "-----", "[INFO]".
PATRON_DECORACION = re.compile(r"^(?:\[\w+\]\s*)?[=\-\s]*|[=\-\s]*$")
# Una advertencia no dice nada de los tests: con mvn -q, la única línea es un aviso de la JVM.
PATRON_ADVERTENCIA = re.compile(r"\bwarn(?:ing)?\b", re.IGNORECASE)


def resumir_tests(salida: str) -> str | None:
    """La línea con el total de tests, o la última línea útil; None si no hay nada."""
    texto = PATRON_ANSI.sub("", salida)
    for patron in PATRONES_RESUMEN:
        encontrados = patron.findall(texto)
        if encontrados:
            return _recortar(encontrados[-1])
    for linea in reversed(texto.splitlines()):
        limpia = PATRON_DECORACION.sub("", linea)
        if limpia and not _es_ruido(limpia):
            return _recortar(limpia)
    return None


def _es_ruido(linea: str) -> bool:
    return (
        linea.startswith((PREFIJO_CODIGO_SALIDA, PREFIJO_TIEMPO_AGOTADO))
        or PATRON_ADVERTENCIA.search(linea) is not None
    )


def seccion_tests(informe: str) -> str:
    """La salida de los tests dentro del informe de validación (sin el lint)."""
    inicio = informe.find(ENCABEZADO_TESTS)
    if inicio < 0:
        return informe
    # La primera línea es el encabezado "## Tests (<comando>)".
    cuerpo = informe[inicio:].split("\n", 1)[1] if "\n" in informe[inicio:] else ""
    fin = cuerpo.find(ENCABEZADO_SECCION)
    return cuerpo if fin < 0 else cuerpo[:fin]


def _recortar(linea: str) -> str:
    linea = linea.strip()
    if len(linea) <= MAX_CARACTERES_RESUMEN:
        return linea
    return linea[:MAX_CARACTERES_RESUMEN - 1] + "…"
