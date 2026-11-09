"""Detección de bucles: el modelo repite lo mismo esperando otro resultado.

Pasó en TIENDA-3: reescribió VentaServiceImpl 4 veces igual, con el mismo test fallando.
A la tercera repetición se le avisa que replantee el enfoque; a la quinta se corta (se
puede retomar con harness resume, por ejemplo con otro modelo).
"""

import hashlib
import re
from collections import Counter
from pathlib import Path

from harness.bucle import ErrorBucle, Paso
from harness.herramientas.comandos import leer_codigo_salida
from harness.herramientas.rutas import ErrorRuta, resolver_ruta

REPETICIONES_PARA_AVISAR = 3
REPETICIONES_PARA_CORTAR = 5
HERRAMIENTAS_ESCRITURA = ("escribir_archivo", "editar_archivo")
HERRAMIENTAS_TESTS = ("ejecutar_tests", "ejecutar_comando")
# Lo que cambia entre dos corridas del mismo error y no importa para compararlas.
PATRONES_VARIABLES = (
    re.compile(r"\d{4}-\d{2}-\d{2}[T ][\d:.,]+Z?"),
    re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:ms|s|sec|seconds|segundos|min)\b"),
    re.compile(r"\b0x[0-9a-fA-F]+\b"),
)


def normalizar_salida(texto: str) -> str:
    """La salida sin horas, duraciones ni direcciones de memoria."""
    for patron in PATRONES_VARIABLES:
        texto = patron.sub("<x>", texto)
    return texto.strip()


class DetectorBucles:
    """Mira cada paso y cuenta las repeticiones; devuelve una nota o lanza ErrorBucle."""

    def __init__(self, repo: Path) -> None:
        self._repo = repo
        # (ruta, hash del archivo después de escribir): cuántas veces quedó así.
        self._escrituras: Counter[tuple[str, str]] = Counter()
        # Por comando: la última salida fallida (normalizada) y cuántas veces seguidas salió.
        self._fallas: dict[str, tuple[str, int]] = {}

    def revisar(self, paso: Paso) -> str | None:
        """La nota para el modelo si el paso repite algo, o None."""
        if paso.es_error:
            return None
        nombre = paso.llamada.nombre
        if nombre in HERRAMIENTAS_ESCRITURA:
            return self._revisar_escritura(str(paso.llamada.argumentos.get("ruta", "")))
        if nombre in HERRAMIENTAS_TESTS:
            comando = f"{nombre} {paso.llamada.argumentos.get('comando', '')}".strip()
            return self._revisar_tests(comando, paso.resultado)
        return None

    def _revisar_escritura(self, ruta: str) -> str | None:
        huella = self._huella(ruta)
        if huella is None:
            return None
        self._escrituras[(ruta, huella)] += 1
        veces = self._escrituras[(ruta, huella)]
        return self._evaluar(veces, f"dejas '{ruta}' con exactamente el mismo contenido")

    def _revisar_tests(self, comando: str, resultado: str) -> str | None:
        salida = leer_codigo_salida(resultado)
        if salida is None or salida == (0, False):
            self._fallas.pop(comando, None)
            return None
        normalizada = normalizar_salida(resultado)
        anterior, veces = self._fallas.get(comando, ("", 0))
        veces = veces + 1 if normalizada == anterior else 1
        self._fallas[comando] = (normalizada, veces)
        return self._evaluar(veces, "los tests fallan con exactamente el mismo error")

    def _evaluar(self, veces: int, que: str) -> str | None:
        if veces >= REPETICIONES_PARA_CORTAR:
            raise ErrorBucle(
                f"El modelo está en un bucle: {veces} veces seguidas {que}. Se corta la "
                "ejecución; se puede retomar con harness resume (por ejemplo, con otro modelo)."
            )
        if veces >= REPETICIONES_PARA_AVISAR:
            return (
                f"Aviso del harness: es la {veces}.ª vez que {que}. Repetir lo mismo no va a "
                "cambiar el resultado: replantea el enfoque (vuelve a leer el error y el código "
                "relacionado, revisa tus supuestos). Si se repite "
                f"{REPETICIONES_PARA_CORTAR} veces, la ejecución se corta."
            )
        return None

    def _huella(self, ruta: str) -> str | None:
        try:
            archivo = resolver_ruta(self._repo, ruta)
            return hashlib.sha256(archivo.read_bytes()).hexdigest()
        except (ErrorRuta, OSError):
            return None
