"""Memoria del proyecto entre ejecuciones: .harness/memory.md y la herramienta guardar_memoria.

El modelo guarda ahí lo que le sirve a un ticket futuro (un comando, una variable que necesitan
los tests, una trampa). El archivo se sube a Git con el resto del ticket, así que la memoria
nueva pasa por la revisión humana del PR: el siguiente ticket, que sale de la rama base, la ve
recién cuando se mergea. Al cargarla en el prompt va como notas (datos), no como instrucciones.
"""

import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from harness.git import ErrorGit, ejecutar_git
from harness.tipos import Herramienta
from harness.variables import MIN_CARACTERES_SECRETO

RUTA_MEMORIA = ".harness/memory.md"
NOMBRE_HERRAMIENTA = "guardar_memoria"
MAX_CARACTERES_NOTA = 500
# Se carga en cada pedido al modelo: un tope evita que se coma los tokens.
MAX_CARACTERES_MEMORIA = 8_000
ENCABEZADO = (
    "# Memoria del proyecto\n\n"
    "Notas que guardó el harness para los próximos tickets. Se pueden corregir o borrar a "
    "mano.\n\n"
)
PREFIJO_NOTA = "- "
# Formas comunes de tokens y contraseñas: una nota así no se guarda, aunque no sea un secreto
# conocido de la ejecución.
PATRON_SECRETO = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(password|passwd|contrase[nñ]a|token|secret|api[_-]?key)\s*[=:]\s*\S{6,}",
    re.IGNORECASE,
)
DESCRIPCION_HERRAMIENTA = (
    "Guarda una nota en la memoria del proyecto (.harness/memory.md) para los próximos "
    "tickets: algo que descubriste del proyecto y que no está en AGENTS.md (un comando, una "
    "variable que necesitan los tests, una trampa del código). Una línea, como máximo "
    f"{MAX_CARACTERES_NOTA} caracteres. Nunca secretos (tokens, contraseñas, API keys). "
    "Sí: 'Los tests necesitan TAREAS_DB_URL en variables_entorno', 'mvn -q oculta los "
    "errores de compilación: correr con -e para verlos'. No: lo que hizo este ticket ('El "
    "repositorio soporta filtrado opcional en listar'), porque ya queda en el código y en el "
    "PR; una nota así se rechaza."
)
# Una nota que repite el título del ticket describe lo que se acaba de hacer, no algo útil
# para el próximo (TAREAS-3). Se comparan las palabras significativas por su comienzo, así
# "filtrarlas" y "filtrado" cuentan como la misma.
MIN_LETRAS_PALABRA = 4
LETRAS_RAIZ = 5
MIN_PALABRAS_REPETIDAS = 2
PROPORCION_TITULO_REPETIDA = 2 / 3
PALABRAS_COMUNES = frozenset({
    "para", "como", "esta", "este", "estos", "estas", "desde", "entre", "sobre", "cada",
    "todo", "todos", "todas", "with", "from", "that", "this", "cuando", "donde",
})


class ErrorMemoria(Exception):
    """La nota no se guardó (vacía, muy larga, con un secreto, o la memoria está llena)."""


@dataclass(frozen=True)
class Memoria:
    """memory.md ya leído (recortado si es muy largo), o nada si el repo no lo tiene."""

    texto: str | None = None
    caracteres_originales: int = 0

    @property
    def es_recortada(self) -> bool:
        return self.caracteres_originales > MAX_CARACTERES_MEMORIA

    @property
    def notas(self) -> int:
        """Cuántas notas tiene (las líneas que empiezan con '- ')."""
        lineas = (self.texto or "").splitlines()
        return sum(1 for linea in lineas if linea.startswith(PREFIJO_NOTA))


def cargar_memoria(repo: Path) -> Memoria:
    """Lee .harness/memory.md del repo; si no existe o está vacío, no hay memoria."""
    ruta = repo / RUTA_MEMORIA
    if not ruta.is_file():
        return Memoria()
    texto = ruta.read_text(encoding="utf-8-sig", errors="replace")
    if not texto.strip():
        return Memoria()
    return Memoria(texto[:MAX_CARACTERES_MEMORIA], len(texto))


def guardar_nota(
    repo: Path,
    nota: str,
    ticket: str,
    secretos: Sequence[str],
    hoy: date | None = None,
    titulo: str = "",
) -> str:
    """Agrega la nota al final de memory.md (lo crea si falta) y devuelve la línea agregada.

    Con `titulo` (el del ticket) rechaza la nota que lo repite: describe este ticket.
    """
    nota = " ".join(nota.split())
    if not nota:
        raise ErrorMemoria("la nota está vacía.")
    if len(nota) > MAX_CARACTERES_NOTA:
        raise ErrorMemoria(
            f"la nota tiene {len(nota)} caracteres; el máximo es {MAX_CARACTERES_NOTA}."
        )
    if _tiene_secreto(nota, secretos):
        raise ErrorMemoria("la nota parece tener un secreto (un token o una contraseña); no se "
                           "guardó. La memoria se sube a Git: nunca guardes secretos.")
    if repite_el_titulo(nota, titulo):
        raise ErrorMemoria(
            f"la nota parece describir lo que hace este ticket ('{titulo}'); no se guardó. "
            "Guarda solo lo que le sirve a un ticket futuro (un comando, una variable, una "
            "trampa del código): lo hecho en este ticket ya queda en el código y en el PR."
        )
    ruta = repo / RUTA_MEMORIA
    actual = ruta.read_text(encoding="utf-8-sig") if ruta.is_file() else ENCABEZADO
    linea = f"{PREFIJO_NOTA}{nota} ({ticket}, {(hoy or date.today()).isoformat()})"
    if len(actual) + len(linea) + 1 > MAX_CARACTERES_MEMORIA:
        raise ErrorMemoria(
            f"la memoria está llena ({MAX_CARACTERES_MEMORIA} caracteres); no se guardó. "
            "El usuario puede depurarla a mano."
        )
    separador = "" if not actual or actual.endswith("\n") else "\n"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(f"{actual}{separador}{linea}\n", encoding="utf-8")
    return linea


def crear_herramienta_memoria(
    repo: Path,
    ticket: str,
    secretos: Sequence[str],
    al_guardar: Callable[[str], None],
    titulo: str = "",
) -> Herramienta:
    """La herramienta guardar_memoria del modelo; escribe desde el host, no desde el contenedor.

    `titulo` es el del ticket: una nota que lo repite se rechaza.
    """

    def guardar_memoria(nota: str) -> str:
        # Un error se lanza: el bucle se lo devuelve al modelo marcado como error.
        linea = guardar_nota(repo, nota, ticket, secretos, titulo=titulo)
        al_guardar(linea)
        return (
            f"Nota guardada en {RUTA_MEMORIA}. Vale para los próximos tickets cuando se "
            "mergee el PR de este."
        )

    parametros = {
        "type": "object",
        "properties": {"nota": {"type": "string", "description": "La nota, en una línea."}},
        "required": ["nota"],
    }
    return Herramienta(NOMBRE_HERRAMIENTA, DESCRIPCION_HERRAMIENTA, parametros, guardar_memoria)


def esta_ignorada(repo: Path) -> bool:
    """True si el .gitignore del repo ignora memory.md (la nota no entraría al commit)."""
    try:
        ejecutar_git(repo, "check-ignore", "--quiet", RUTA_MEMORIA)
    except ErrorGit:
        return False  # No está ignorada (código 1) o el repo no usa Git.
    return True


def notas_nuevas(repo: Path, base: str) -> list[str]:
    """Las notas que el ticket agregó respecto de la rama base (para la descripción del PR)."""
    try:
        diferencia = ejecutar_git(repo, "diff", "--unified=0", base, "--", RUTA_MEMORIA)
    except ErrorGit:
        return []
    agregadas = f"+{PREFIJO_NOTA}"
    return [linea[1:] for linea in diferencia.splitlines() if linea.startswith(agregadas)]


def repite_el_titulo(nota: str, titulo: str) -> bool:
    """True si la nota tiene al menos 2/3 de las palabras significativas del título (y no
    menos de MIN_PALABRAS_REPETIDAS)."""
    del_titulo = _raices(titulo)
    if len(del_titulo) < MIN_PALABRAS_REPETIDAS:
        return False
    repetidas = len(del_titulo & _raices(nota))
    necesarias = max(
        MIN_PALABRAS_REPETIDAS, math.ceil(len(del_titulo) * PROPORCION_TITULO_REPETIDA)
    )
    return repetidas >= necesarias


def _raices(texto: str) -> set[str]:
    """El comienzo de cada palabra significativa, en minúsculas y sin tildes."""
    sin_tildes = "".join(
        letra for letra in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(letra) != "Mn"
    )
    palabras = re.findall(r"[a-z]+", sin_tildes)
    return {
        palabra[:LETRAS_RAIZ] for palabra in palabras
        if len(palabra) >= MIN_LETRAS_PALABRA and palabra not in PALABRAS_COMUNES
    }


def _tiene_secreto(nota: str, secretos: Sequence[str]) -> bool:
    conocidos = (s for s in secretos if len(s) >= MIN_CARACTERES_SECRETO)
    return any(secreto in nota for secreto in conocidos) or bool(PATRON_SECRETO.search(nota))
