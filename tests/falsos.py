"""Dobles de prueba: modelo falso, entorno Docker falso y respuestas guionadas."""

from collections.abc import Callable
from typing import Any

from harness.entorno_docker import ResultadoComando
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, RespuestaModelo


class EntornoFalso:
    """Imita el contenedor: devuelve resultados guionados y guarda los comandos recibidos.

    `efecto` simula lo que el comando hace en el repo montado (por ejemplo, tocar un archivo).
    """

    def __init__(
        self, resultados: list[ResultadoComando], efecto: Callable[[], None] | None = None
    ) -> None:
        self._resultados = list(resultados)
        self._efecto = efecto
        self.comandos: list[str] = []

    def ejecutar(self, comando: str) -> ResultadoComando:
        self.comandos.append(comando)
        if self._efecto:
            self._efecto()
        return self._resultados.pop(0)


class ProveedorFalso:
    """Devuelve respuestas guionadas, sin llamar a ninguna API, y guarda lo que recibió.

    Ignora `herramienta_obligatoria` (como un proveedor sin tool_choice), pero la guarda.
    """

    def __init__(self, respuestas: list[RespuestaModelo | BaseException]) -> None:
        self._respuestas = list(respuestas)
        self.conversaciones: list[list[Mensaje]] = []
        # Nombres de las herramientas ofrecidas en cada pedido.
        self.herramientas: list[list[str]] = []
        self.obligatorias: list[str | None] = []

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[Herramienta],
        herramienta_obligatoria: str | None = None,
    ) -> RespuestaModelo:
        self.conversaciones.append(list(mensajes))
        self.herramientas.append([h.nombre for h in herramientas])
        self.obligatorias.append(herramienta_obligatoria)
        respuesta = self._respuestas.pop(0)
        # Un error en el guion se lanza: así se simula un proveedor caído.
        if isinstance(respuesta, BaseException):
            raise respuesta
        return respuesta

    def adaptar_esquema(self, esquema: dict[str, Any]) -> dict[str, Any]:
        return esquema


def pedir(*llamadas: LlamadaHerramienta) -> RespuestaModelo:
    """Respuesta del modelo que pide herramientas."""
    return RespuestaModelo(None, list(llamadas), tokens_entrada=10, tokens_salida=5)


def terminar(texto: str = "Listo") -> RespuestaModelo:
    """Respuesta del modelo que da por terminado el trabajo."""
    return RespuestaModelo(texto, [], tokens_entrada=10, tokens_salida=5)


PLAN_SIMPLE = {
    "resumen": "Agregar el módulo nuevo.",
    "archivos": [{"ruta": "nuevo.py", "accion": "crear", "motivo": "Lo pide el ticket."}],
    "pasos": ["Crear nuevo.py"],
    "tests": [],
}


def proponer(plan: dict | None = None, id_llamada: str = "plan") -> RespuestaModelo:
    """Respuesta del modelo que llama a proponer_plan (por defecto, con PLAN_SIMPLE)."""
    llamada = LlamadaHerramienta(id_llamada, "proponer_plan", plan or PLAN_SIMPLE)
    return pedir(llamada)


def vacia(texto: str | None = None) -> RespuestaModelo:
    """Respuesta del modelo sin texto (o solo espacios) y sin herramientas."""
    return RespuestaModelo(texto, [], tokens_entrada=10, tokens_salida=0)
