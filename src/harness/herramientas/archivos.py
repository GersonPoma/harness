"""Herramientas de archivos que se le ofrecen al modelo."""

from collections.abc import Callable, Sequence
from pathlib import Path

from harness.herramientas.rutas import resolver_ruta, validar_escritura

# Limita lo que se manda al modelo: los planes gratis tienen pocos tokens por minuto.
MAX_CARACTERES_LECTURA = 12_000
MAX_ENTRADAS_LISTADO = 200
CARPETAS_IGNORADAS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".idea"}


class ErrorHerramienta(Exception):
    """Error al ejecutar una herramienta; su mensaje se le devuelve al modelo."""


def leer_archivo(repo: Path, ruta: str) -> str:
    """Devuelve el contenido de un archivo de texto del repo."""
    archivo = resolver_ruta(repo, ruta)
    if not archivo.is_file():
        raise ErrorHerramienta(f"No existe el archivo '{ruta}'.")
    try:
        contenido = archivo.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ErrorHerramienta(f"'{ruta}' no es un archivo de texto UTF-8.") from error
    if len(contenido) > MAX_CARACTERES_LECTURA:
        return (
            f"{contenido[:MAX_CARACTERES_LECTURA]}\n"
            f"[... recortado: se muestran {MAX_CARACTERES_LECTURA} de {len(contenido)} "
            f"caracteres]"
        )
    return contenido


def escribir_archivo(
    repo: Path,
    ruta: str,
    contenido: str,
    protegidos: Sequence[str] = (),
    al_escribir: Callable[[str], None] | None = None,
) -> str:
    """Crea o reemplaza un archivo del repo, creando las carpetas que falten.

    `protegidos` son los globs de archivos_protegidos; `al_escribir` recibe la ruta relativa
    escrita (el entorno Docker la usa para saber si debe reinstalar dependencias).
    """
    archivo = resolver_ruta(repo, ruta)
    validar_escritura(repo, archivo, protegidos)
    if archivo.is_dir():
        raise ErrorHerramienta(f"'{ruta}' es una carpeta, no un archivo.")
    archivo.parent.mkdir(parents=True, exist_ok=True)
    # newline="" para escribir los saltos de línea tal cual vienen, también en Windows.
    archivo.write_text(contenido, encoding="utf-8", newline="")
    if al_escribir:
        al_escribir(archivo.relative_to(repo.resolve()).as_posix())
    return f"Archivo '{ruta}' escrito ({len(contenido)} caracteres)."


def listar_directorio(repo: Path, ruta: str = ".") -> str:
    """Lista el contenido de una carpeta del repo; las carpetas terminan en '/'."""
    carpeta = resolver_ruta(repo, ruta)
    if not carpeta.is_dir():
        raise ErrorHerramienta(f"No existe la carpeta '{ruta}'.")
    entradas = sorted(
        (e for e in carpeta.iterdir() if e.name not in CARPETAS_IGNORADAS),
        key=lambda e: (not e.is_dir(), e.name.lower()),
    )
    if not entradas:
        return f"La carpeta '{ruta}' está vacía."
    nombres = [f"{e.name}/" if e.is_dir() else e.name for e in entradas]
    if len(nombres) > MAX_ENTRADAS_LISTADO:
        sobrantes = len(nombres) - MAX_ENTRADAS_LISTADO
        nombres = [*nombres[:MAX_ENTRADAS_LISTADO], f"[... y {sobrantes} entradas más]"]
    return "\n".join(nombres)
