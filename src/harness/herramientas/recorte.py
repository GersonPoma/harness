"""Recorte de salidas largas de comandos antes de devolvérselas al modelo.

Conservar solo el final no alcanza: en Maven, Gradle o Spring el error real puede quedar tapado
por cientos de líneas de logs que vienen después. Por eso se priorizan las líneas que parecen
errores (con un poco de contexto) y, con lo que queda, el final de la salida.
"""

import re

# Genéricos y de los stacks más comunes: Maven/JUnit ([ERROR], "expected: X but was: Y"),
# Java y Python ("java.lang.IllegalStateException: ...", "ValueError: ...", "Caused by",
# Traceback), pytest ("E   assert", "archivo.py:12:", FAILED) y Gradle/npm (FAIL, "Error: ...").
# Las excepciones solo cuentan seguidas de ':', para no tomar un nombre de clase en un log.
PATRON_ERROR = re.compile(
    r"ERROR|FAIL|[\w.$]+(?:Exception|Error)\b:|^\s*Error:|Caused by|Traceback|^E\s"
    r"|^\S+\.py:\d+:"
    r"|(?i:expected|but was)"
)
# Texto fijo que Maven agrega a todo build fallido: no dice nada del error.
PATRON_IGNORADO = re.compile(
    r"^\[ERROR\]\s*$|\[Help \d+\]|Re-run Maven|re-run Maven|See dump files"
    r"|For more information about the errors"
)
LINEAS_ANTES = 2
LINEAS_DESPUES = 3
# Parte del tope que se guarda para el final aunque haya muchos errores: ahí está el resumen.
PORCION_FINAL = 0.3
# Una línea enorme (un JSON, un classpath) no debe comerse todo el tope.
MAX_CARACTERES_LINEA = 500
# Lo que ocupa, como mucho, una marca "[... N líneas omitidas ...]" con su salto de línea.
CARACTERES_POR_OMISION = 40


def recortar_salida(salida: str, max_caracteres: int) -> str:
    """Si la salida pasa del tope, deja las líneas de error con contexto y el final."""
    if len(salida) <= max_caracteres:
        return salida
    lineas = [_acortar_linea(linea) for linea in salida.splitlines()]
    elegidas = _elegir_errores(lineas, max_caracteres)
    _completar_con_el_final(lineas, elegidas, max_caracteres)
    texto = _unir_con_omisiones(lineas, elegidas)
    # Respaldo para salidas raras (pocas líneas enormes, o un tope diminuto): el final tal cual,
    # sin pasar del tope.
    return texto if len(texto) <= max_caracteres else salida[-max_caracteres:]


def _acortar_linea(linea: str) -> str:
    if len(linea) <= MAX_CARACTERES_LINEA:
        return linea
    return f"{linea[:MAX_CARACTERES_LINEA]} [... línea recortada]"


def _elegir_errores(lineas: list[str], max_caracteres: int) -> set[int]:
    """Bloques de error con su contexto, en orden: el primero suele ser la causa."""
    tope = max_caracteres - int(max_caracteres * PORCION_FINAL)
    elegidas: set[int] = set()
    usado = 0
    for numero, linea in enumerate(lineas):
        if not _es_error(linea):
            continue
        desde = max(0, numero - LINEAS_ANTES)
        hasta = min(len(lineas), numero + LINEAS_DESPUES + 1)
        nuevas = [i for i in range(desde, hasta) if i not in elegidas]
        costo = _costo(lineas, nuevas) + CARACTERES_POR_OMISION
        # Si un bloque no entra se sigue con los demás: uno más corto todavía puede entrar.
        if usado + costo > tope:
            continue
        elegidas.update(nuevas)
        usado += costo
    return elegidas


def _es_error(linea: str) -> bool:
    return bool(PATRON_ERROR.search(linea)) and not PATRON_IGNORADO.search(linea)


def _completar_con_el_final(lineas: list[str], elegidas: set[int], max_caracteres: int) -> None:
    """Agrega líneas desde el final hacia atrás mientras alcance el tope.

    Salta el texto fijo de Maven, que ocupa las últimas líneas sin decir nada del error.
    """
    usado = _costo(lineas, elegidas) + CARACTERES_POR_OMISION * _cantidad_de_bloques(elegidas)
    for numero in range(len(lineas) - 1, -1, -1):
        if numero in elegidas or PATRON_IGNORADO.search(lineas[numero]):
            continue
        costo = len(lineas[numero]) + 1
        # Si la línea de abajo queda afuera, entre las dos va una marca de omisión.
        if numero + 1 < len(lineas) and numero + 1 not in elegidas:
            costo += CARACTERES_POR_OMISION
        if usado + costo + CARACTERES_POR_OMISION > max_caracteres:
            break
        elegidas.add(numero)
        usado += costo


def _unir_con_omisiones(lineas: list[str], elegidas: set[int]) -> str:
    partes: list[str] = []
    omitidas = 0
    for numero, linea in enumerate(lineas):
        if numero not in elegidas:
            omitidas += 1
            continue
        if omitidas:
            partes.append(_marca_omision(omitidas))
            omitidas = 0
        partes.append(linea)
    if omitidas:
        partes.append(_marca_omision(omitidas))
    return "\n".join(partes)


def _marca_omision(cantidad: int) -> str:
    lineas = "línea omitida" if cantidad == 1 else "líneas omitidas"
    return f"[... {cantidad} {lineas} ...]"


def _costo(lineas: list[str], numeros: set[int] | list[int]) -> int:
    return sum(len(lineas[numero]) + 1 for numero in numeros)


def _cantidad_de_bloques(elegidas: set[int]) -> int:
    return sum(1 for numero in elegidas if numero - 1 not in elegidas)
