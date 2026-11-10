"""Proveedor para la API de Anthropic (Claude), con el SDK oficial `anthropic`.

Traduce los tipos propios al formato de Messages: el system va aparte, los resultados de
herramientas son bloques tool_result de un mensaje del usuario y los roles tienen que
alternarse (los mensajes seguidos del mismo rol se unen en uno).
"""

import re
import time
from collections.abc import Callable
from typing import Any

import anthropic

from harness.config import ConfigModelo, donde_configurar
from harness.proveedores.base import ErrorProveedor
from harness.proveedores.esquemas import quitar_extensiones
from harness.proveedores.reintentos import (
    MAX_REINTENTOS,
    ErrorPasajero,
    Reintentos,
    espera_de_retry_after,
)
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, RespuestaModelo, Rol

# Por debajo del límite en que el SDK exige streaming para un pedido sin stream; alcanza para
# escribir archivos largos con escribir_archivo.
MAX_TOKENS_RESPUESTA = 16_000
# Una respuesta de 16.000 tokens puede tardar minutos: más que los 120 s de los otros.
TIMEOUT_PEDIDO_SEGUNDOS = 300.0
# Caché automática del prompt: en un bucle de 10 a 30 pedidos con el mismo comienzo, la
# entrada repetida se cobra mucho menos.
ES_CON_CACHE_DEL_PROMPT = True
CACHE_DEL_PROMPT = {"type": "ephemeral"}
ESTADO_LIMITE = 429
ESTADO_ERROR_SERVIDOR = 500
# Anthropic responde 529 cuando está sobrecargado.
ESTADO_SOBRECARGA = 529
STOP_MAX_TOKENS = "max_tokens"
# Los ids de tool_use solo admiten estos caracteres; los de otro proveedor (respaldo, resume)
# se normalizan igual en tool_use y en tool_result, así siguen emparejados.
PATRON_ID_INVALIDO = re.compile(r"[^a-zA-Z0-9_-]")
ERROR_CORTADA = (
    "La respuesta se cortó por max_tokens antes de terminar esta llamada: sus argumentos "
    "están incompletos. Vuelve a llamarla (si escribías un archivo largo, divídelo con "
    "editar_archivo)."
)
ESQUEMA_VACIO: dict[str, Any] = {"type": "object", "properties": {}}

_ROLES = {Rol.USUARIO: "user", Rol.ASISTENTE: "assistant", Rol.HERRAMIENTA: "user"}


class ProveedorAnthropic:
    """Cumple ProveedorLLM con la API de Anthropic y reintenta ante errores pasajeros."""

    def __init__(
        self,
        config: ConfigModelo,
        cliente: Any | None = None,
        dormir: Callable[[float], None] = time.sleep,
        reloj: Callable[[], float] = time.monotonic,
        al_reintentar: Callable[[str], None] | None = None,
        max_reintentos: int = MAX_REINTENTOS,
    ) -> None:
        self._config = config
        self.id = f"{config.proveedor}/{config.modelo}"
        # max_retries=0: los reintentos los maneja Reintentos, con sus propias esperas.
        self._cliente = cliente or anthropic.Anthropic(
            api_key=config.api_key, max_retries=0, timeout=TIMEOUT_PEDIDO_SEGUNDOS
        )
        self._reintentos = Reintentos(
            config.proveedor, max_reintentos, dormir, reloj, al_reintentar
        )

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[Herramienta],
        herramienta_obligatoria: str | None = None,
    ) -> RespuestaModelo:
        """Envía la conversación a Claude y devuelve su respuesta en tipos propios."""
        sistema = "\n\n".join(m.contenido or "" for m in mensajes if m.rol == Rol.SISTEMA)
        parametros: dict[str, Any] = {
            "model": self._config.modelo,
            "max_tokens": MAX_TOKENS_RESPUESTA,
            "messages": a_mensajes_anthropic(mensajes),
        }
        if sistema:
            parametros["system"] = sistema
        if ES_CON_CACHE_DEL_PROMPT:
            parametros["cache_control"] = CACHE_DEL_PROMPT
        if herramientas:
            parametros["tools"] = [self._a_herramienta(h) for h in herramientas]
            if herramienta_obligatoria:
                parametros["tool_choice"] = {"type": "tool", "name": herramienta_obligatoria}
        try:
            respuesta = self._reintentos.pedir(
                lambda: self._cliente.messages.create(**parametros), _clasificar,
                (anthropic.APIError,),
            )
        except anthropic.APIError as error:
            raise self._error_permanente(error) from error
        return a_respuesta_modelo(respuesta, self.id)

    def adaptar_esquema(self, esquema: dict[str, Any]) -> dict[str, Any]:
        """Sin claves "x-..." y con "type": "object" en la raíz, que input_schema exige (una
        herramienta MCP sin parámetros puede traer {}). $ref y $defs se aceptan tal cual."""
        adaptado = quitar_extensiones(esquema) or {}
        if adaptado.get("type") != "object":
            adaptado = {**adaptado, "type": "object"}
        adaptado.setdefault("properties", {})
        return adaptado

    def _a_herramienta(self, herramienta: Herramienta) -> dict[str, Any]:
        return {
            "name": herramienta.nombre,
            "description": herramienta.descripcion,
            "input_schema": self.adaptar_esquema(herramienta.parametros),
        }

    def _error_permanente(self, error: anthropic.APIError) -> ErrorProveedor:
        proveedor = self._config.proveedor
        if isinstance(error, anthropic.AuthenticationError):
            variable = self._config.variable_api_key or "la API key"
            return ErrorProveedor(
                f"El proveedor '{proveedor}' rechazó la API key: revisa "
                f"{donde_configurar(variable)}."
            )
        estado = getattr(error, "status_code", "desconocido")
        return ErrorProveedor(f"'{proveedor}' respondió con error {estado}: {error.message}")


def a_mensajes_anthropic(mensajes: list[Mensaje]) -> list[dict[str, Any]]:
    """La conversación sin el system, con los roles alternados (se unen los seguidos)."""
    resultado: list[dict[str, Any]] = []
    for mensaje in mensajes:
        if mensaje.rol == Rol.SISTEMA:
            continue
        bloques = _bloques(mensaje)
        if not bloques:
            continue
        rol = _ROLES[mensaje.rol]
        if resultado and resultado[-1]["role"] == rol:
            resultado[-1]["content"].extend(bloques)
        else:
            resultado.append({"role": rol, "content": bloques})
    return resultado


def _bloques(mensaje: Mensaje) -> list[dict[str, Any]]:
    if mensaje.rol == Rol.HERRAMIENTA:
        return [{
            "type": "tool_result",
            "tool_use_id": normalizar_id(mensaje.id_llamada or ""),
            "content": mensaje.contenido or "",
        }]
    bloques: list[dict[str, Any]] = []
    if (mensaje.contenido or "").strip():
        bloques.append({"type": "text", "text": mensaje.contenido})
    for llamada in mensaje.llamadas:
        bloques.append({
            "type": "tool_use",
            "id": normalizar_id(llamada.id),
            "name": llamada.nombre,
            "input": llamada.argumentos,
        })
    return bloques


def normalizar_id(id_llamada: str) -> str:
    """El id con solo [a-zA-Z0-9_-] (los de otros proveedores pueden traer otros)."""
    return PATRON_ID_INVALIDO.sub("_", id_llamada) or "llamada"


def a_respuesta_modelo(respuesta: Any, origen: str) -> RespuestaModelo:
    """La respuesta del SDK en tipos propios."""
    textos: list[str] = []
    llamadas: list[LlamadaHerramienta] = []
    for bloque in respuesta.content:
        if bloque.type == "text":
            textos.append(bloque.text)
        elif bloque.type == "tool_use":
            argumentos = bloque.input if isinstance(bloque.input, dict) else {}
            llamadas.append(LlamadaHerramienta(bloque.id, bloque.name, argumentos, origen=origen))
    if respuesta.stop_reason == STOP_MAX_TOKENS and llamadas:
        # La última llamada pudo quedar a medias: mejor que el modelo la repita.
        llamadas[-1].error_argumentos = ERROR_CORTADA
    uso = respuesta.usage
    entrada = (
        (getattr(uso, "input_tokens", 0) or 0)
        + (getattr(uso, "cache_creation_input_tokens", 0) or 0)
        + (getattr(uso, "cache_read_input_tokens", 0) or 0)
    )
    return RespuestaModelo(
        texto="".join(textos) or None,
        llamadas=llamadas,
        tokens_entrada=entrada,
        tokens_salida=getattr(uso, "output_tokens", 0) or 0,
    )


def _clasificar(error: BaseException) -> ErrorPasajero | None:
    """429, 529, 5xx, sin conexión y tiempo agotado se reintentan; el resto no."""
    if isinstance(error, anthropic.APITimeoutError):
        return ErrorPasajero("sin respuesta (tiempo agotado)")
    if isinstance(error, anthropic.APIConnectionError):
        return ErrorPasajero("sin conexión")
    estado = getattr(error, "status_code", None)
    respuesta = getattr(error, "response", None)
    espera = espera_de_retry_after(
        respuesta.headers.get("retry-after") if respuesta is not None else None
    )
    if estado == ESTADO_LIMITE:
        return ErrorPasajero(f"con límite de pedidos ({estado})", es_limite=True,
                             espera_sugerida=espera)
    if estado == ESTADO_SOBRECARGA:
        return ErrorPasajero(f"sobrecargado ({estado})", espera_sugerida=espera)
    if estado is not None and estado >= ESTADO_ERROR_SERVIDOR:
        return ErrorPasajero(f"saturado ({estado})", espera_sugerida=espera)
    return None
