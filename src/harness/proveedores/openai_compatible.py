"""Proveedor para cualquier API con formato OpenAI (Gemini, Groq, OpenRouter, OpenAI...)."""

import json
import re
import time
from collections.abc import Callable
from typing import Any

import openai

from harness.config import ConfigModelo, donde_configurar
from harness.proveedores.base import ErrorProveedor
from harness.proveedores.esquemas import quitar_extensiones, resolver_referencias
from harness.proveedores.presets import PRESETS, PerfilEsquema
from harness.proveedores.reintentos import (
    MAX_REINTENTOS,
    ErrorPasajero,
    Reintentos,
    espera_de_retry_after,
)
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, RespuestaModelo, Rol

# Gemini gratis permite 15 pedidos por minuto: uno cada 4 segundos como mínimo.
INTERVALO_MINIMO_SEGUNDOS = 4.0
# El SDK espera 600 s por defecto: un pedido colgado congelaría la ejecución 10 minutos por
# cada reintento. Una respuesta normal tarda segundos.
TIMEOUT_PEDIDO_SEGUNDOS = 120.0
ESTADO_LIMITE = 429
ESTADO_ERROR_SERVIDOR = 500
# Un 429 de cuota diaria no se recupera esperando (se renueva una vez por día). Se reconoce
# por el cuerpo: Gemini manda un quotaId con "PerDay" y OpenRouter "free-models-per-day".
PATRON_CUOTA_DIARIA = re.compile(r"per[\s_-]?day", re.IGNORECASE)
# Gemini a veces genera una llamada a herramienta que no puede leer y responde sin texto ni
# llamadas, con este finish_reason. Pasa sobre todo con llamadas de otro proveedor en la
# conversación (firma de reemplazo): repetir el mismo pedido suele alcanzar.
FINISH_LLAMADA_MALFORMADA = "MALFORMED_FUNCTION_CALL"
MAX_REPETICIONES_MALFORMADA = 2

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
        max_reintentos: int = MAX_REINTENTOS,
    ) -> None:
        self._config = config
        self.id = f"{config.proveedor}/{config.modelo}"
        self._perfil_esquema = PRESETS[config.proveedor].perfil_esquema
        self._firma_de_reemplazo = PRESETS[config.proveedor].firma_de_reemplazo
        self._es_con_tool_choice = PRESETS[config.proveedor].es_con_tool_choice
        # max_retries=0: los reintentos los maneja esta clase, con sus propias esperas.
        self._cliente = cliente or openai.OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
            timeout=TIMEOUT_PEDIDO_SEGUNDOS,
        )
        # Con un respaldo detrás conviene rendirse antes: el siguiente proveedor sí responde.
        self._reintentos = Reintentos(
            config.proveedor, max_reintentos, dormir, reloj, al_reintentar,
            INTERVALO_MINIMO_SEGUNDOS,
        )

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[Herramienta],
        herramienta_obligatoria: str | None = None,
    ) -> RespuestaModelo:
        """Envía la conversación al modelo y devuelve su respuesta en tipos propios."""
        parametros: dict[str, Any] = {
            "model": self._config.modelo,
            "messages": [self._a_mensaje_openai(m) for m in mensajes],
        }
        if herramientas:
            parametros["tools"] = [self._a_herramienta_openai(h) for h in herramientas]
            if herramienta_obligatoria and self._es_con_tool_choice:
                parametros["tool_choice"] = {
                    "type": "function",
                    "function": {"name": herramienta_obligatoria},
                }
        respuesta = self._pedir_sin_llamada_malformada(parametros)
        return _a_respuesta_modelo(respuesta, self.id)

    def adaptar_esquema(self, esquema: dict[str, Any]) -> dict[str, Any]:
        """Sin claves "x-..." para todos (no se sabe si cada API las acepta); según el preset,
        además sin $ref ni $defs."""
        adaptado = quitar_extensiones(esquema)
        if self._perfil_esquema == PerfilEsquema.GEMINI:
            adaptado = resolver_referencias(adaptado)
        return adaptado

    def _a_herramienta_openai(self, herramienta: Herramienta) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": herramienta.nombre,
                "description": herramienta.descripcion,
                "parameters": self.adaptar_esquema(herramienta.parametros),
            },
        }

    def _pedir_sin_llamada_malformada(self, parametros: dict[str, Any]) -> Any:
        # La respuesta descartada no tiene salida; su entrada no se suma a los tokens.
        for repeticion in range(1, MAX_REPETICIONES_MALFORMADA + 1):
            respuesta = self._pedir_con_reintentos(parametros)
            if not _es_llamada_malformada(respuesta):
                return respuesta
            self._reintentos.avisar(
                f"{self._config.proveedor.capitalize()} generó una llamada a herramienta mal "
                f"formada ({FINISH_LLAMADA_MALFORMADA}): se repite el pedido "
                f"({repeticion}/{MAX_REPETICIONES_MALFORMADA})."
            )
        # Si sigue igual, el bucle la trata como respuesta vacía.
        return self._pedir_con_reintentos(parametros)

    def _pedir_con_reintentos(self, parametros: dict[str, Any]) -> Any:
        try:
            return self._reintentos.pedir(
                lambda: self._cliente.chat.completions.create(**parametros), _clasificar,
                (openai.APIError,),
            )
        except openai.APIError as error:
            raise self._error_permanente(error) from error

    def _error_permanente(self, error: openai.APIError) -> ErrorProveedor:
        proveedor = self._config.proveedor
        if isinstance(error, openai.AuthenticationError):
            variable = self._config.variable_api_key or "la API key"
            return ErrorProveedor(
                f"El proveedor '{proveedor}' rechazó la API key: revisa "
                f"{donde_configurar(variable)}."
            )
        estado = getattr(error, "status_code", "desconocido")
        return ErrorProveedor(f"'{proveedor}' respondió con error {estado}: {error.message}")

    def _a_mensaje_openai(self, mensaje: Mensaje) -> dict[str, Any]:
        resultado: dict[str, Any] = {
            "role": _ROLES_OPENAI[mensaje.rol],
            "content": mensaje.contenido,
        }
        if mensaje.llamadas:
            resultado["tool_calls"] = [self._a_llamada_openai(ll) for ll in mensaje.llamadas]
        if mensaje.rol == Rol.HERRAMIENTA:
            resultado["tool_call_id"] = mensaje.id_llamada
        return resultado

    def _a_llamada_openai(self, llamada: LlamadaHerramienta) -> dict[str, Any]:
        return {
            # Primero los datos del proveedor, para que nunca pisen los campos estándar.
            **self._datos_a_reenviar(llamada),
            "id": llamada.id,
            "type": "function",
            "function": {
                "name": llamada.nombre,
                "arguments": json.dumps(llamada.argumentos, ensure_ascii=False),
            },
        }

    def _datos_a_reenviar(self, llamada: LlamadaHerramienta) -> dict[str, Any]:
        """Los datos propios solo vuelven a quien los generó (sin origen: se asume que es él)."""
        if llamada.origen is None or llamada.origen == self.id:
            return llamada.datos_proveedor or {}
        if self._firma_de_reemplazo:
            return {"extra_content": {"google": {"thought_signature": self._firma_de_reemplazo}}}
        return {}


def _clasificar(error: BaseException) -> ErrorPasajero | None:
    """Los errores que pueden resolverse esperando; None para los demás."""
    if not isinstance(error, openai.APIError) or not _es_pasajero(error):
        return None
    return ErrorPasajero(
        _describir(error),
        es_limite=getattr(error, "status_code", None) == ESTADO_LIMITE,
        es_cuota_diaria=_es_cuota_diaria(error),
        espera_sugerida=_espera_sugerida(error),
    )


def _es_cuota_diaria(error: openai.APIError) -> bool:
    if getattr(error, "status_code", None) != ESTADO_LIMITE:
        return False
    cuerpo = json.dumps(getattr(error, "body", None), ensure_ascii=False, default=str)
    return PATRON_CUOTA_DIARIA.search(f"{error.message} {cuerpo}") is not None


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
    return espera_de_retry_after(
        respuesta.headers.get("retry-after") if respuesta is not None else None
    )


def _es_llamada_malformada(respuesta: Any) -> bool:
    eleccion = respuesta.choices[0]
    motivo = str(getattr(eleccion, "finish_reason", None) or "")
    mensaje = eleccion.message
    return (
        FINISH_LLAMADA_MALFORMADA in motivo
        and not mensaje.tool_calls
        and not (mensaje.content or "").strip()
    )


def _a_respuesta_modelo(respuesta: Any, origen: str) -> RespuestaModelo:
    mensaje = respuesta.choices[0].message
    llamadas = [
        _a_llamada(llamada, indice, origen)
        for indice, llamada in enumerate(mensaje.tool_calls or [])
    ]
    uso = respuesta.usage
    return RespuestaModelo(
        texto=mensaje.content,
        llamadas=llamadas,
        tokens_entrada=uso.prompt_tokens if uso else 0,
        tokens_salida=uso.completion_tokens if uso else 0,
    )


def _a_llamada(llamada: Any, indice: int, origen: str) -> LlamadaHerramienta:
    # Algunos proveedores compatibles no mandan id; sin id no se puede asociar el resultado.
    id_llamada = llamada.id or f"llamada_{indice}"
    # model_extra trae los campos fuera del estándar OpenAI (Gemini 3 manda ahí extra_content
    # con la thought_signature, que exige recibir de vuelta en el siguiente pedido).
    datos_proveedor = getattr(llamada, "model_extra", None) or None
    argumentos, error = _leer_argumentos(llamada.function.arguments)
    return LlamadaHerramienta(
        id_llamada, llamada.function.name, argumentos, error, datos_proveedor, origen
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
