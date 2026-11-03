"""Proveedores conocidos: formato de API, URL base y variable de la API key."""

from dataclasses import dataclass
from enum import StrEnum


class FormatoApi(StrEnum):
    """Formato de API que habla el proveedor; define qué clase lo implementa."""

    OPENAI = "openai"


class PerfilEsquema(StrEnum):
    """Qué le acepta la API a los esquemas de las herramientas (ver adaptar_esquema)."""

    # Sin claves "x-..."; el resto, tal cual.
    GENERAL = "general"
    # Además, sin $ref ni $defs: Gemini no los acepta.
    GEMINI = "gemini"


@dataclass(frozen=True)
class Preset:
    """Datos fijos de un proveedor conocido."""

    formato: FormatoApi
    base_url: str | None
    variable_api_key: str | None
    perfil_esquema: PerfilEsquema = PerfilEsquema.GENERAL


PROVEEDOR_PERSONALIZADO = "personalizado"

# Agregar un proveedor con formato OpenAI es agregar una fila aquí.
PRESETS: dict[str, Preset] = {
    "gemini": Preset(
        FormatoApi.OPENAI,
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        PerfilEsquema.GEMINI,
    ),
    "groq": Preset(FormatoApi.OPENAI, "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": Preset(FormatoApi.OPENAI, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": Preset(FormatoApi.OPENAI, None, "OPENAI_API_KEY"),
    # La URL y la variable de la key las escribe el usuario en el .env.
    PROVEEDOR_PERSONALIZADO: Preset(FormatoApi.OPENAI, None, None),
}
