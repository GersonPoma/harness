"""Qué archivos del repo objetivo cambiaron durante una ejecución.

Se compara una foto (hash de cada archivo) del inicio con otra del final, en vez de seguir
solo a escribir_archivo: un comando del contenedor también puede crear o modificar archivos.
No depende de Git, así que funciona aunque el repo no lo use.
"""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from harness.carpetas import CARPETAS_IGNORADAS

# Además de las ignoradas: lo que escriben el harness (logs) y las herramientas de tests, que
# no es trabajo del modelo.
CARPETAS_SIN_SEGUIMIENTO = CARPETAS_IGNORADAS | {
    ".harness", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

Instantanea = dict[str, str]


@dataclass
class CambiosRepo:
    """Rutas relativas (con '/') de los archivos que cambiaron, ordenadas."""

    creados: list[str] = field(default_factory=list)
    modificados: list[str] = field(default_factory=list)
    eliminados: list[str] = field(default_factory=list)

    @property
    def hay_cambios(self) -> bool:
        """True si se creó, modificó o eliminó al menos un archivo."""
        return bool(self.creados or self.modificados or self.eliminados)


def tomar_instantanea(repo: Path) -> Instantanea:
    """Hash de cada archivo del repo, por ruta relativa."""
    raiz = repo.resolve()
    instantanea: Instantanea = {}
    for carpeta, subcarpetas, archivos in os.walk(raiz):
        subcarpetas[:] = [s for s in subcarpetas if s not in CARPETAS_SIN_SEGUIMIENTO]
        base = Path(carpeta).relative_to(raiz)
        for nombre in archivos:
            ruta = Path(carpeta) / nombre
            try:
                instantanea[(base / nombre).as_posix()] = _hash(ruta)
            except OSError:
                continue  # Se borró o no se puede leer justo ahora: no es un cambio del modelo.
    return instantanea


def comparar(antes: Instantanea, despues: Instantanea) -> CambiosRepo:
    """Diferencias entre dos fotos del repo."""
    return CambiosRepo(
        creados=sorted(despues.keys() - antes.keys()),
        modificados=sorted(r for r in antes.keys() & despues.keys() if antes[r] != despues[r]),
        eliminados=sorted(antes.keys() - despues.keys()),
    )


def _hash(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()
