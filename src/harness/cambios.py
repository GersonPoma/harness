"""Qué archivos del repo objetivo cambiaron durante una ejecución.

Se compara una foto (hash de cada archivo) del inicio con otra del final, en vez de seguir
solo a escribir_archivo: un comando del contenedor también puede crear o modificar archivos.
Si el repo usa Git, la foto respeta su .gitignore; si no, se usa una lista fija de carpetas.
"""

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from harness.carpetas import CARPETAS_IGNORADAS
from harness.memoria import RUTA_MEMORIA

# Además de las ignoradas: lo que escriben el harness (logs) y las herramientas de tests, que
# no es trabajo del modelo aunque el .gitignore del proyecto no lo excluya. La memoria sí se
# sigue (es lo único de .harness que entra en el commit), en su propio grupo.
CARPETAS_SIN_SEGUIMIENTO = CARPETAS_IGNORADAS | {
    ".harness", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

# Solo sin Git: salidas de compilación de Maven, Gradle, npm y de algunos IDEs (bin/ de
# Eclipse y VS Code). Con Git manda el .gitignore, que sabe cuáles son las de cada proyecto.
CARPETAS_DE_COMPILACION = {"target", "build", "dist", "out", ".gradle", "bin"}

# Archivos versionados y nuevos, sin los que excluye el .gitignore. Con -z las rutas salen
# separadas por '\0' y sin comillas ni escapes, aunque tengan tildes o espacios.
ARGUMENTOS_LS_FILES = ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]
TIMEOUT_GIT_SEGUNDOS = 30

Instantanea = dict[str, str]


@dataclass
class CambiosRepo:
    """Rutas relativas (con '/') de los archivos que cambiaron, ordenadas."""

    creados: list[str] = field(default_factory=list)
    modificados: list[str] = field(default_factory=list)
    eliminados: list[str] = field(default_factory=list)
    # .harness/memory.md, si cambió: la escribe guardar_memoria, no el código del ticket.
    memoria: list[str] = field(default_factory=list)

    @property
    def hay_cambios(self) -> bool:
        """True si se creó, modificó o eliminó al menos un archivo (la memoria incluida)."""
        return bool(self.creados or self.modificados or self.eliminados or self.memoria)

    @property
    def total(self) -> int:
        """Cuántos archivos cambiaron: los mismos que entran en el commit."""
        return len(self.creados) + len(self.modificados) + len(self.eliminados) + len(
            self.memoria
        )


def tomar_instantanea(repo: Path) -> Instantanea:
    """Hash de cada archivo del repo, por ruta relativa."""
    raiz = repo.resolve()
    instantanea: Instantanea = {}
    for ruta in listar_archivos(raiz):
        try:
            instantanea[ruta] = _hash(raiz / ruta)
        except OSError:
            continue  # Se borró o no se puede leer justo ahora: no es un cambio del modelo.
    return instantanea


def listar_archivos(raiz: Path) -> list[str]:
    """Rutas relativas de los archivos del repo que se siguen (las de la foto de cambios).

    Con Git, las que no ignora el .gitignore; sin Git, una lista fija de carpetas ignoradas.
    """
    rutas = _rutas_segun_git(raiz)
    return _rutas_sin_git(raiz) if rutas is None else rutas


def comparar(antes: Instantanea, despues: Instantanea) -> CambiosRepo:
    """Diferencias entre dos fotos del repo; la memoria va aparte, en su propio grupo."""
    creados = despues.keys() - antes.keys()
    modificados = {r for r in antes.keys() & despues.keys() if antes[r] != despues[r]}
    eliminados = antes.keys() - despues.keys()
    memoria = {RUTA_MEMORIA} & (creados | modificados | eliminados)
    return CambiosRepo(
        creados=sorted(creados - memoria),
        modificados=sorted(modificados - memoria),
        eliminados=sorted(eliminados - memoria),
        memoria=sorted(memoria),
    )


def _rutas_segun_git(raiz: Path) -> list[str] | None:
    """Rutas que Git no ignora, o None si el repo no es un repositorio Git."""
    try:
        proceso = subprocess.run(
            ["git", "-C", str(raiz), *ARGUMENTOS_LS_FILES],
            capture_output=True,
            timeout=TIMEOUT_GIT_SEGUNDOS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None  # Git no está instalado o no responde: se usa la lista fija.
    if proceso.returncode != 0:
        return None
    rutas = proceso.stdout.decode("utf-8", errors="replace").split("\0")
    return [ruta for ruta in rutas if ruta and not _esta_sin_seguimiento(ruta)]


def _esta_sin_seguimiento(ruta: str) -> bool:
    if ruta == RUTA_MEMORIA:
        return False
    carpetas = ruta.split("/")[:-1]
    return any(carpeta in CARPETAS_SIN_SEGUIMIENTO for carpeta in carpetas)


def _rutas_sin_git(raiz: Path) -> list[str]:
    ignoradas = CARPETAS_SIN_SEGUIMIENTO | CARPETAS_DE_COMPILACION
    rutas: list[str] = []
    for carpeta, subcarpetas, archivos in os.walk(raiz):
        subcarpetas[:] = [s for s in subcarpetas if s not in ignoradas]
        base = Path(carpeta).relative_to(raiz)
        rutas.extend((base / nombre).as_posix() for nombre in archivos)
    if (raiz / RUTA_MEMORIA).is_file():
        rutas.append(RUTA_MEMORIA)
    return rutas


def _hash(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()
