"""Dobles de prueba: modelo falso y respuestas guionadas."""

from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, RespuestaModelo


class ProveedorFalso:
    """Devuelve respuestas guionadas, sin llamar a ninguna API, y guarda lo que recibió."""

    def __init__(self, respuestas: list[RespuestaModelo]) -> None:
        self._respuestas = list(respuestas)
        self.conversaciones: list[list[Mensaje]] = []

    def responder(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> RespuestaModelo:
        self.conversaciones.append(list(mensajes))
        return self._respuestas.pop(0)


def pedir(*llamadas: LlamadaHerramienta) -> RespuestaModelo:
    """Respuesta del modelo que pide herramientas."""
    return RespuestaModelo(None, list(llamadas), tokens_entrada=10, tokens_salida=5)


def terminar(texto: str = "Listo") -> RespuestaModelo:
    """Respuesta del modelo que da por terminado el trabajo."""
    return RespuestaModelo(texto, [], tokens_entrada=10, tokens_salida=5)
