"""buscar_en_codigo: dónde aparece un texto en el repo, sin leer archivo por archivo."""

import re
from pathlib import Path, PurePosixPath

from harness.cambios import listar_archivos
from harness.herramientas.archivos import ErrorHerramienta
from harness.herramientas.rutas import ErrorRuta, resolver_ruta
from harness.secretos import es_archivo_de_secretos

# Limita lo que se manda al modelo, como leer_archivo.
MAX_COINCIDENCIAS = 100
MAX_CARACTERES_LINEA = 200
# Un archivo más grande casi nunca es código (datos, minificados): no se recorre.
MAX_BYTES_ARCHIVO = 1_000_000
ELIPSIS = "…"


def buscar_en_codigo(repo: Path, patron: str, ruta: str = ".", es_regex: bool = False) -> str:
    """Líneas "ruta:línea: texto" donde aparece el patrón, dentro de la carpeta o archivo dado.

    Recorre los mismos archivos que la foto de cambios (con Git, los que no ignora el
    .gitignore), sin .git, sin archivos de secretos y sin binarios.
    """
    if not patron:
        raise ErrorHerramienta("El patrón está vacío.")
    try:
        expresion = re.compile(patron if es_regex else re.escape(patron))
    except re.error as error:
        raise ErrorHerramienta(f"La expresión regular no es válida: {error}.") from error
    raiz = repo.resolve()
    base = resolver_ruta(repo, ruta).relative_to(raiz).as_posix()
    coincidencias = [
        linea
        for relativa in _dentro_de(listar_archivos(raiz), base)
        for linea in _buscar_en_archivo(repo, relativa, expresion)
    ]
    if not coincidencias:
        return f"Sin coincidencias de '{patron}' en '{ruta}'."
    visibles = coincidencias[:MAX_COINCIDENCIAS]
    resto = len(coincidencias) - len(visibles)
    if resto:
        visibles.append(f"[... y {resto} coincidencias más: acota la búsqueda con 'ruta']")
    return "\n".join(visibles)


def _dentro_de(rutas: list[str], base: str) -> list[str]:
    if base == ".":
        return sorted(rutas)
    return sorted(r for r in rutas if r == base or r.startswith(f"{base}/"))


def _buscar_en_archivo(repo: Path, relativa: str, expresion: re.Pattern[str]) -> list[str]:
    if es_archivo_de_secretos(PurePosixPath(relativa).name):
        return []
    try:
        # Por resolver_ruta también: un symlink del repo que apunta afuera no se lee.
        archivo = resolver_ruta(repo, relativa)
        if not archivo.is_file() or archivo.stat().st_size > MAX_BYTES_ARCHIVO:
            return []
        texto = archivo.read_bytes().decode("utf-8")
    except (ErrorRuta, OSError, UnicodeDecodeError):
        return []
    return [
        f"{relativa}:{numero}: {_recortar(linea.strip())}"
        for numero, linea in enumerate(texto.splitlines(), start=1)
        if expresion.search(linea)
    ]


def _recortar(linea: str) -> str:
    if len(linea) <= MAX_CARACTERES_LINEA:
        return linea
    return linea[:MAX_CARACTERES_LINEA - len(ELIPSIS)] + ELIPSIS
