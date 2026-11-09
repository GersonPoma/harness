"""Proveedor para cualquier API con formato OpenAI (Gemini, Groq, OpenRouter, OpenAI...)."""

import json
import re
import time
from collections.abc import Callable
from typing import Any

import openai

from harness.config import ConfigModelo
from harness.proveedores.base import (
    MOTIVO_CAIDO,
    MOTIVO_CUOTA_DIARIA,
    MOTIVO_LIMITE,
    ErrorProveedor,
    ErrorProveedorCaido,
)
from harness.proveedores.esquemas import quitar_extensiones, resolver_referencias
from harness.proveedores.presets import PRESETS, PerfilEsquema
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
# Un 429 de cuota diaria no se recupera esperando (se renueva una vez por día). Se reconoce
# por el cuerpo: Gemini manda un quotaId con "PerDay" y OpenRouter "free-models-per-day".
PATRON_CUOTA_DIARIA = re.compile(r"per[\s_-]?day", re.IGNORECASE)
# Gemini a veces genera una llamada a herramienta que no puede leer y responde sin texto ni
# llamadas, con este finish_reason. Pasa sobre todo con llamadas de otro proveedor en la
# conversación (firma de reemplazo): repetir el mismo pedido suele alcanzar.
FINISH_LLAMADA_MALFORMADA = "MALFORMED_FUNCTION_CALL"
MAX_REPETICIONES_MALFORMADA = 2

SUGERENCIA_OTRO_PROVEEDOR = (
    "configura otro proveedor en HARNESS_RESPALDO (o cambia HARNESS_PROVEEDOR / HARNESS_MODELO) "
    "en el .env."
)

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
        # Con un respaldo detrás conviene rendirse antes: el siguiente proveedor sí responde.
        self._max_reintentos = max_reintentos
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
        self._dormir = dormir
        self._reloj = reloj
        # Avisa de cada reintento para que una espera larga no parezca un cuelgue.
        self._al_reintentar = al_reintentar
        self._ultimo_pedido: float | None = None

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
            if self._al_reintentar:
                self._al_reintentar(
                    f"{self._config.proveedor.capitalize()} generó una llamada a herramienta mal "
                    f"formada ({FINISH_LLAMADA_MALFORMADA}): se repite el pedido "
                    f"({repeticion}/{MAX_REPETICIONES_MALFORMADA})."
                )
        # Si sigue igual, el bucle la trata como respuesta vacía.
        return self._pedir_con_reintentos(parametros)

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
                if _es_cuota_diaria(error):
                    raise self._error_cuota_diaria() from error
                if reintento == self._max_reintentos:
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
                f"reintento {reintento}/{self._max_reintentos} en {segundos:g} s..."
            )

    def _error_permanente(self, error: openai.APIError) -> ErrorProveedor:
        proveedor = self._config.proveedor
        if isinstance(error, openai.AuthenticationError):
            return ErrorProveedor(
                f"El proveedor '{proveedor}' rechazó la API key: revisa el .env."
            )
        estado = getattr(error, "status_code", "desconocido")
        return ErrorProveedor(f"'{proveedor}' respondió con error {estado}: {error.message}")

    def _error_agotado(self, error: openai.APIError) -> ErrorProveedorCaido:
        proveedor = self._config.proveedor
        reintentos = self._max_reintentos
        if getattr(error, "status_code", None) == ESTADO_LIMITE:
            return ErrorProveedorCaido(
                f"Se agotó el límite de pedidos del proveedor '{proveedor}' (error 429) después "
                f"de {reintentos} reintentos. Espera un rato o {SUGERENCIA_OTRO_PROVEEDOR}",
                MOTIVO_LIMITE,
            )
        return ErrorProveedorCaido(
            f"El servicio de '{proveedor}' no está disponible ({_describir(error)}) después de "
            f"{reintentos} reintentos. Intenta más tarde o {SUGERENCIA_OTRO_PROVEEDOR}",
            MOTIVO_CAIDO,
        )

    def _error_cuota_diaria(self) -> ErrorProveedorCaido:
        return ErrorProveedorCaido(
            f"Se agotó la cuota diaria del proveedor '{self._config.proveedor}' (error 429): "
            f"no se recupera esperando unos minutos, se renueva una vez por día. "
            f"Mañana, o ahora mismo si {SUGERENCIA_OTRO_PROVEEDOR}",
            MOTIVO_CUOTA_DIARIA,
        )

    def _respetar_intervalo(self) -> None:
        ahora = self._reloj()
        if self._ultimo_pedido is not None:
            faltante = INTERVALO_MINIMO_SEGUNDOS - (ahora - self._ultimo_pedido)
            if faltante > 0:
                self._dormir(faltante)
                ahora += faltante
        self._ultimo_pedido = ahora


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
    valor = respuesta.headers.get("retry-after") if respuesta is not None else None
    try:
        return min(float(valor), ESPERA_MAXIMA_SEGUNDOS) if valor else None
    except ValueError:
        return None


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
