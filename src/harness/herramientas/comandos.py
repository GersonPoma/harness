"""Herramientas que ejecutan comandos dentro del contenedor del proyecto."""

from collections.abc import Sequence
from typing import Protocol

from harness.entorno_docker import ResultadoComando
from harness.herramientas.archivos import ErrorHerramienta
from harness.herramientas.protegidos import GuardiaProtegidos

# Con sh -c, cualquiera de estos permite encadenar un programa no permitido detrás de uno
# permitido ('pytest & rm -rf /app', o un salto de línea). '&' y '|' cubren '&&' y '||'.
CARACTERES_PROHIBIDOS = (";", "&", "|", ">", "<", "`", "$(", "\n", "\r")


class Entorno(Protocol):
    """Lo que las herramientas necesitan del entorno Docker."""

    def ejecutar(self, comando: str) -> ResultadoComando: ...


def validar_comando(comando: str, permitidos: Sequence[str]) -> None:
    """Lanza ErrorHerramienta si el comando no está permitido para el modelo."""
    for caracter in CARACTERES_PROHIBIDOS:
        if caracter in comando:
            visible = repr(caracter) if caracter in "\n\r" else f"'{caracter}'"
            raise ErrorHerramienta(
                f"El comando contiene {visible}: no se permite encadenar ni redirigir comandos."
            )
    palabras = comando.split()
    if not palabras:
        raise ErrorHerramienta("El comando está vacío.")
    if palabras[0] not in permitidos:
        raise ErrorHerramienta(
            f"'{palabras[0]}' no está permitido. Programas permitidos: {', '.join(permitidos)}."
        )


def recortar_salida(salida: str, max_caracteres: int) -> str:
    """Conserva el final de la salida, donde suelen estar los errores."""
    if len(salida) <= max_caracteres:
        return salida
    recortados = len(salida) - max_caracteres
    return f"[... se recortaron los primeros {recortados} caracteres]\n{salida[-max_caracteres:]}"


def formatear_resultado(
    resultado: ResultadoComando, max_caracteres: int, timeout: int | None = None
) -> str:
    """Texto que recibe el modelo: código de salida y salida recortada."""
    if resultado.es_tiempo_agotado:
        encabezado = f"Tiempo agotado: el comando se cortó a los {timeout} s."
    else:
        encabezado = f"Código de salida: {resultado.codigo}"
    salida = recortar_salida(resultado.salida.strip(), max_caracteres)
    return f"{encabezado}\n{salida}" if salida else encabezado


class EjecutorComandos:
    """Corre comandos en el contenedor y vigila los archivos protegidos después de cada uno."""

    def __init__(
        self,
        entorno: Entorno,
        guardia: GuardiaProtegidos,
        max_caracteres: int,
        timeout: int,
    ) -> None:
        self._entorno = entorno
        self._guardia = guardia
        self._max_caracteres = max_caracteres
        self._timeout = timeout

    def correr(self, comando: str) -> tuple[bool, str]:
        """Devuelve (pasó, texto para el modelo)."""
        resultado = self._entorno.ejecutar(comando)
        texto = formatear_resultado(resultado, self._max_caracteres, self._timeout)
        avisos = self._guardia.revisar()
        if avisos:
            texto += "\n\nAVISO: " + "\n".join(avisos)
        return resultado.codigo == 0 and not resultado.es_tiempo_agotado, texto


def ejecutar_comando(ejecutor: EjecutorComandos, permitidos: Sequence[str], comando: str) -> str:
    """Herramienta: ejecuta un programa de la lista de permitidos."""
    validar_comando(comando, permitidos)
    return ejecutor.correr(comando)[1]


def validar_proyecto(
    ejecutor: EjecutorComandos, tests: str, lint: str | None
) -> tuple[bool, str]:
    """Corre tests y lint (si hay); devuelve (todo pasó, informe para el modelo)."""
    es_valido, salida_tests = ejecutor.correr(tests)
    informe = f"## Tests ({tests})\n{salida_tests}"
    if lint:
        es_lint_valido, salida_lint = ejecutor.correr(lint)
        es_valido = es_valido and es_lint_valido
        informe += f"\n\n## Lint ({lint})\n{salida_lint}"
    return es_valido, informe
