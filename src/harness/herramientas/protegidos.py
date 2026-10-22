"""Vigila que los comandos del contenedor no modifiquen archivos protegidos.

escribir_archivo ya rechaza los archivos protegidos, pero con 'python' (o con un test escrito
por el modelo) se pueden modificar desde el contenedor. Por eso se guarda una copia al
arrancar y se compara después de cada comando.
"""

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

from harness.globs import coincide_alguno
from harness.herramientas.archivos import CARPETAS_IGNORADAS


class GuardiaProtegidos:
    """Copia de los archivos protegidos para detectar y deshacer cambios."""

    def __init__(self, repo: Path, patrones: Sequence[str]) -> None:
        self._repo = repo.resolve()
        self._patrones = list(patrones)
        self._copias: dict[str, bytes] = {}
        self._hashes: dict[str, str] = {}
        for ruta in self._buscar():
            contenido = (self._repo / ruta).read_bytes()
            self._copias[ruta] = contenido
            self._hashes[ruta] = _hash(contenido)

    def revisar(self) -> list[str]:
        """Restaura lo que cambió, borra lo que se creó y devuelve un aviso por archivo."""
        if not self._patrones:
            return []
        avisos = [self._restaurar(ruta) for ruta in self._hashes if self._cambio(ruta)]
        for ruta in sorted(self._buscar() - self._hashes.keys()):
            (self._repo / ruta).unlink()
            avisos.append(f"'{ruta}' es una ruta protegida: el archivo creado se eliminó.")
        return avisos

    def _cambio(self, ruta: str) -> bool:
        archivo = self._repo / ruta
        return not archivo.is_file() or _hash(archivo.read_bytes()) != self._hashes[ruta]

    def _restaurar(self, ruta: str) -> str:
        archivo = self._repo / ruta
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_bytes(self._copias[ruta])
        return f"'{ruta}' es un archivo protegido: se modificó o borró y se restauró."

    def _buscar(self) -> set[str]:
        """Rutas relativas de los archivos que hoy coinciden con los patrones."""
        if not self._patrones:
            return set()
        encontrados = set()
        for carpeta, subcarpetas, archivos in os.walk(self._repo):
            subcarpetas[:] = [s for s in subcarpetas if s not in CARPETAS_IGNORADAS]
            base = Path(carpeta).relative_to(self._repo)
            for nombre in archivos:
                ruta = (base / nombre).as_posix()
                if coincide_alguno(ruta, self._patrones):
                    encontrados.add(ruta)
        return encontrados


def _hash(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()
