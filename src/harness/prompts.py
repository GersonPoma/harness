"""Textos que se le mandan al modelo."""

from harness.contexto import MAX_CARACTERES_AGENTS, ContextoProyecto
from harness.tipos import Mensaje, Rol

PROMPT_SISTEMA = """\
Eres un agente de programación que implementa tickets en un repositorio de código.
Trabajas solo a través de las herramientas que se te ofrecen; todas las rutas son relativas \
a la raíz del repo y usan '/' como separador.

Antes de empezar:
- Lee el contexto del proyecto, al final de estas instrucciones.
- Explora solo lo necesario para entender dónde va el cambio.

Al trabajar:
- Respeta las convenciones y el estilo del código existente.
- Para modificar un archivo, léelo primero y luego escríbelo completo con el cambio.
- Si una herramienta devuelve un error, analízalo y corrige la llamada (salvo los rechazos \
de seguridad: ver abajo).
- No puedes modificar harness.yaml ni nada dentro de .git/.
- No puedes leer ni escribir archivos .env o .env.* (pueden tener secretos); .env.example sí.

Límites de seguridad:
- Tus herramientas solo acceden al repo objetivo. Si una herramienta rechaza algo por \
seguridad (ruta fuera del repo, archivo protegido), no busques otra forma de hacerlo: \
explica por qué no se puede y termina.

Cuando termines, responde sin llamar herramientas, con un resumen breve de los archivos \
que creaste o modificaste y por qué."""


# Solo se agrega si el repo tiene harness.yaml: sin él no hay contenedor ni tests.
PROMPT_VALIDACION = """

Tests:
- Los comandos corren en un contenedor Docker con el repo montado en la carpeta de trabajo.
- Usa ejecutar_tests para comprobar tu trabajo antes de terminar.
- Cuando termines, el harness correrá los tests (y el lint, si hay). Si fallan, recibirás \
la salida y tendrás que corregir el código."""


# El harness lee AGENTS.md y lo pone aquí: así no depende de que el modelo lo pida.
PROMPT_CON_AGENTS = """

Contexto del proyecto (AGENTS.md de la raíz del repo; ya lo tienes, no lo vuelvas a leer):

{agents_md}"""

AVISO_AGENTS_RECORTADO = """

[AGENTS.md se recortó: se muestran {mostrados} de {total} caracteres. Si necesitas el resto, \
léelo con leer_archivo.]"""

PROMPT_SIN_AGENTS = """

Contexto del proyecto: el repo no tiene AGENTS.md. Explora con listar_directorio y \
leer_archivo para entender la estructura y las convenciones antes de cambiar nada."""


MENSAJE_RESPUESTA_VACIA = (
    "Tu respuesta llegó vacía. Si terminaste, explica qué hiciste; si no, sigue trabajando."
)


def crear_mensajes_iniciales(
    ticket: str, contexto: ContextoProyecto | None = None, hay_validacion: bool = False
) -> list[Mensaje]:
    """Arma la conversación inicial: instrucciones, contexto del proyecto y ticket."""
    sistema = PROMPT_SISTEMA + (PROMPT_VALIDACION if hay_validacion else "")
    sistema += crear_seccion_contexto(contexto or ContextoProyecto())
    return [
        Mensaje(Rol.SISTEMA, sistema),
        Mensaje(Rol.USUARIO, f"Implementa el siguiente ticket:\n\n{ticket}"),
    ]


def crear_seccion_contexto(contexto: ContextoProyecto) -> str:
    """Texto con AGENTS.md (o el aviso de que falta) que va al final del system prompt."""
    if contexto.agents_md is None:
        return PROMPT_SIN_AGENTS
    seccion = PROMPT_CON_AGENTS.format(agents_md=contexto.agents_md.strip())
    if contexto.es_recortado:
        seccion += AVISO_AGENTS_RECORTADO.format(
            mostrados=MAX_CARACTERES_AGENTS, total=contexto.caracteres_originales
        )
    return seccion


def crear_mensaje_validacion_fallida(informe: str, intento: int, max_intentos: int) -> str:
    """Mensaje que recibe el modelo cuando los tests o el lint fallan al terminar."""
    return (
        f"La validación falló (corrección {intento} de {max_intentos}). Revisa la salida, "
        f"corrige el código y vuelve a terminar cuando los tests pasen.\n\n{informe}"
    )
