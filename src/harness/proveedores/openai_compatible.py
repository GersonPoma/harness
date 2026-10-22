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
MAX_REINTENTOS = 5
ESPERA_INICIAL_SEGUNDOS = 5.0
ESPERA_MAXIMA_SEGUNDOS = 60.0
# El SDK espera 600 s por defecto: un pedido colgado congelaría la ejecución 10 minutos por
# cada reintento. Una respuesta normal tarda segundos.
TIMEOUT_PEDIDO_SEGUNDOS = 120.0
ESTADO_LIMITE = 429
ESTADO_ERROR_SERVIDOR = 500

_ROLES_OPENAI = {
    Rol.SISTEMA: "system",
    Rol.USUARIO: "user",
    Rol.ASISTENTE: "assistant",
    Rol.HERRAMIENTA: "tool",
}


class ProveedorOpenAICompatible:
    """Traduce los tipos propios al formato OpenAI y reintenta ante errores pasajeros."""

    def __init__(
        self,
        config: ConfigModelo,
        cliente: Any | None = None,
        dormir: Callable[[float], None] = time.sleep,
        reloj: Callable[[], float] = time.monotonic,
        al_reintentar: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        # max_retries=0: los reintentos los maneja esta clase, con sus propias esperas.
        self._cliente = cliente or openai.OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
            timeout=TIMEOUT_PEDIDO_SEGUNDOS,
        )
        self._dormir = dormir
        self._reloj = reloj
        # Avisa de cada reintento para que una espera larga no parezca un cuelgue.
        self._al_reintentar = al_reintentar
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
        espera = ESPERA_INICIAL_SEGUNDOS
        reintento = 0
        while True:
            self._respetar_intervalo()
            try:
                return self._cliente.chat.completions.create(**parametros)
            except openai.APIError as error:
                if not _es_pasajero(error):
                    raise self._error_permanente(error) from error
                if reintento == MAX_REINTENTOS:
                    raise self._error_agotado(error) from error
                reintento += 1
                segundos = _espera_sugerida(error) or espera
                self._avisar_reintento(error, reintento, segundos)
                self._dormir(segundos)
                espera = min(espera * 2, ESPERA_MAXIMA_SEGUNDOS)

    def _avisar_reintento(self, error: openai.APIError, reintento: int, segundos: float) -> None:
        if self._al_reintentar:
            self._al_reintentar(
                f"{self._config.proveedor.capitalize()} {_describir(error)}, "
                f"reintento {reintento}/{MAX_REINTENTOS} en {segundos:g} s..."
            )

    def _error_permanente(self, error: openai.APIError) -> ErrorProveedor:
        proveedor = self._config.proveedor
        if isinstance(error, openai.AuthenticationError):
            return ErrorProveedor(
                f"El proveedor '{proveedor}' rechazó la API key: revisa el .env."
            )
        estado = getattr(error, "status_code", "desconocido")
        return ErrorProveedor(f"'{proveedor}' respondió con error {estado}: {error.message}")

    def _error_agotado(self, error: openai.APIError) -> ErrorProveedor:
        proveedor = self._config.proveedor
        sugerencia = (
            "prueba con otro proveedor o modelo cambiando HARNESS_PROVEEDOR / HARNESS_MODELO "
            "en el .env."
        )
        if getattr(error, "status_code", None) == ESTADO_LIMITE:
            return ErrorProveedor(
                f"Se agotó el límite o la cuota del proveedor '{proveedor}' (error 429) después "
                f"de {MAX_REINTENTOS} reintentos. Espera un rato o {sugerencia}"
            )
        return ErrorProveedor(
            f"El servicio de '{proveedor}' no está disponible ({_describir(error)}) después de "
            f"{MAX_REINTENTOS} reintentos. Intenta más tarde o {sugerencia}"
        )

    def _respetar_intervalo(self) -> None:
        ahora = self._reloj()
        if self._ultimo_pedido is not None:
            faltante = INTERVALO_MINIMO_SEGUNDOS - (ahora - self._ultimo_pedido)
            if faltante > 0:
                self._dormir(faltante)
                ahora += faltante
        self._ultimo_pedido = ahora


def _es_pasajero(error: openai.APIError) -> bool:
    """429, errores 5xx, falta de conexión y timeouts pueden resolverse solos esperando."""
    if isinstance(error, openai.APIConnectionError):
        return True
    estado = getattr(error, "status_code", None)
    return estado == ESTADO_LIMITE or (estado is not None and estado >= ESTADO_ERROR_SERVIDOR)


def _describir(error: openai.APIError) -> str:
    if isinstance(error, openai.APITimeoutError):
        return "sin respuesta (tiempo agotado)"
    if isinstance(error, openai.APIConnectionError):
        return "sin conexión"
    estado = getattr(error, "status_code", None)
    if estado == ESTADO_LIMITE:
        return f"con límite de pedidos ({estado})"
    return f"saturado ({estado})"


def _espera_sugerida(error: openai.APIError) -> float | None:
    """Segundos que pide esperar el servidor en Retry-After, si los manda."""
    respuesta = getattr(error, "response", None)
    valor = respuesta.headers.get("retry-after") if respuesta is not None else None
    try:
        return min(float(valor), ESPERA_MAXIMA_SEGUNDOS) if valor else None
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
