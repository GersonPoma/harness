"""Proveedor para cualquier API con formato OpenAI (Gemini, Groq, OpenRouter, OpenAI...)."""

import json
import time
from collections.abc import Callable
from typing import Any

import openai

from harness.config import ConfigModelo
from harness.proveedores.base import ErrorProveedor
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, RespuestaModelo, Rol

# Gemini gratis permite 15 pedidos por minuto: uno cada 4 segundos como mínimo.
INTERVALO_MINIMO_SEGUNDOS = 4.0
MAX_REINTENTOS_429 = 5
ESPERA_INICIAL_429_SEGUNDOS = 5.0
ESPERA_MAXIMA_429_SEGUNDOS = 60.0

_ROLES_OPENAI = {
    Rol.SISTEMA: "system",
    Rol.USUARIO: "user",
    Rol.ASISTENTE: "assistant",
    Rol.HERRAMIENTA: "tool",
}


class ProveedorOpenAICompatible:
    """Traduce los tipos propios al formato OpenAI y reintenta ante errores 429."""

    def __init__(
        self,
        config: ConfigModelo,
        cliente: Any | None = None,
        dormir: Callable[[float], None] = time.sleep,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        # max_retries=0: los reintentos los maneja esta clase, con sus propias esperas.
        self._cliente = cliente or openai.OpenAI(
            api_key=config.api_key, base_url=config.base_url, max_retries=0
        )
        self._dormir = dormir
        self._reloj = reloj
        self._ultimo_pedido: float | None = None

    def responder(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> RespuestaModelo:
        """Envía la conversación al modelo y devuelve su respuesta en tipos propios."""
        parametros: dict[str, Any] = {
            "model": self._config.modelo,
            "messages": [_a_mensaje_openai(m) for m in mensajes],
        }
        if herramientas:
            parametros["tools"] = [_a_herramienta_openai(h) for h in herramientas]
        respuesta = self._pedir_con_reintentos(parametros)
        return _a_respuesta_modelo(respuesta)

    def _pedir_con_reintentos(self, parametros: dict[str, Any]) -> Any:
        espera = ESPERA_INICIAL_429_SEGUNDOS
        for intento in range(MAX_REINTENTOS_429 + 1):
            self._respetar_intervalo()
            try:
                return self._cliente.chat.completions.create(**parametros)
            except openai.RateLimitError as error:
                if intento == MAX_REINTENTOS_429:
                    break
                self._dormir(_espera_sugerida(error) or espera)
                espera = min(espera * 2, ESPERA_MAXIMA_429_SEGUNDOS)
            except openai.AuthenticationError as error:
                raise ErrorProveedor(
                    f"El proveedor '{self._config.proveedor}' rechazó la API key: revisa el .env."
                ) from error
            except openai.APIConnectionError as error:
                raise ErrorProveedor(
                    f"No se pudo conectar con '{self._config.proveedor}': revisa tu internet."
                ) from error
            except openai.APIStatusError as error:
                raise ErrorProveedor(
                    f"'{self._config.proveedor}' respondió con error {error.status_code}: "
                    f"{error.message}"
                ) from error
        raise ErrorProveedor(
            f"Se agotó el límite o la cuota del proveedor '{self._config.proveedor}' "
            f"(error 429) después de {MAX_REINTENTOS_429} reintentos. Espera un rato o prueba "
            f"con otro proveedor o modelo cambiando HARNESS_PROVEEDOR / HARNESS_MODELO en el .env."
        )

    def _respetar_intervalo(self) -> None:
        ahora = self._reloj()
        if self._ultimo_pedido is not None:
            faltante = INTERVALO_MINIMO_SEGUNDOS - (ahora - self._ultimo_pedido)
            if faltante > 0:
                self._dormir(faltante)
                ahora += faltante
        self._ultimo_pedido = ahora


def _espera_sugerida(error: openai.RateLimitError) -> float | None:
    """Segundos que pide esperar el servidor en Retry-After, si los manda."""
    valor = error.response.headers.get("retry-after") if error.response is not None else None
    try:
        return min(float(valor), ESPERA_MAXIMA_429_SEGUNDOS) if valor else None
    except ValueError:
        return None


def _a_mensaje_openai(mensaje: Mensaje) -> dict[str, Any]:
    resultado: dict[str, Any] = {
        "role": _ROLES_OPENAI[mensaje.rol],
        "content": mensaje.contenido,
    }
    if mensaje.llamadas:
        resultado["tool_calls"] = [_a_llamada_openai(llamada) for llamada in mensaje.llamadas]
    if mensaje.rol == Rol.HERRAMIENTA:
        resultado["tool_call_id"] = mensaje.id_llamada
    return resultado


def _a_llamada_openai(llamada: LlamadaHerramienta) -> dict[str, Any]:
    return {
        # Primero los datos del proveedor, para que nunca pisen los campos estándar.
        **(llamada.datos_proveedor or {}),
        "id": llamada.id,
        "type": "function",
        "function": {
            "name": llamada.nombre,
            "arguments": json.dumps(llamada.argumentos, ensure_ascii=False),
        },
    }


def _a_herramienta_openai(herramienta: Herramienta) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": herramienta.nombre,
            "description": herramienta.descripcion,
            "parameters": herramienta.parametros,
        },
    }


def _a_respuesta_modelo(respuesta: Any) -> RespuestaModelo:
    mensaje = respuesta.choices[0].message
    llamadas = [
        _a_llamada(llamada, indice) for indice, llamada in enumerate(mensaje.tool_calls or [])
    ]
    uso = respuesta.usage
    return RespuestaModelo(
        texto=mensaje.content,
        llamadas=llamadas,
        tokens_entrada=uso.prompt_tokens if uso else 0,
        tokens_salida=uso.completion_tokens if uso else 0,
    )


def _a_llamada(llamada: Any, indice: int) -> LlamadaHerramienta:
    # Algunos proveedores compatibles no mandan id; sin id no se puede asociar el resultado.
    id_llamada = llamada.id or f"llamada_{indice}"
    # model_extra trae los campos fuera del estándar OpenAI (Gemini 3 manda ahí extra_content
    # con la thought_signature, que exige recibir de vuelta en el siguiente pedido).
    datos_proveedor = getattr(llamada, "model_extra", None) or None
    argumentos, error = _leer_argumentos(llamada.function.arguments)
    return LlamadaHerramienta(
        id_llamada, llamada.function.name, argumentos, error, datos_proveedor
    )


def _leer_argumentos(texto: str | None) -> tuple[dict[str, Any], str | None]:
    """Convierte los argumentos JSON del modelo en dict; si son inválidos, devuelve el error."""
    try:
        argumentos = json.loads(texto or "{}")
    except json.JSONDecodeError as error:
        return {}, f"Argumentos con JSON inválido: {error}"
    if not isinstance(argumentos, dict):
        return {}, "Los argumentos deben ser un objeto JSON."
    return argumentos, None
