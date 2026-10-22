"""Validación de rutas: el modelo solo puede tocar archivos dentro del repo objetivo."""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from harness.globs import coincide_alguno

# Siempre protegidos contra escritura (docs/harness-yaml.md, archivos_protegidos).
# harness.yaml para que el modelo no pueda aflojar sus propios límites.
ARCHIVOS_PROTEGIDOS = ("harness.yaml",)
CARPETAS_PROTEGIDAS = (".git", ".harness/sessions", ".harness/logs")
# .git ni siquiera se puede leer: no aporta al modelo y puede contener credenciales.
CARPETAS_PROHIBIDAS = (".git",)
# .env y .env.* tampoco: suelen tener secretos reales y su contenido iría a la API del modelo.
# .env.example es la plantilla sin valores reales que se sube a Git.
ARCHIVO_SECRETOS = ".env"
EXCEPCIONES_SECRETOS = (".env.example",)


class ErrorRuta(Exception):
    """La ruta pedida por el modelo no está permitida."""


def resolver_ruta(repo: Path, ruta: str) -> Path:
    """Convierte una ruta relativa del modelo en absoluta, validando que quede en el repo."""
    if Path(ruta).anchor:
        raise ErrorRuta(f"'{ruta}' es una ruta absoluta; usa rutas relativas a la raíz del repo.")
    repo_resuelto = repo.resolve()
    # resolve() sigue los ../ y los symlinks, así se detecta cualquier salida del repo.
    destino = (repo_resuelto / ruta).resolve()
    if not destino.is_relative_to(repo_resuelto):
        raise ErrorRuta(f"'{ruta}' está fuera del repo objetivo.")
    if _esta_dentro_de(_relativa(repo_resuelto, destino), CARPETAS_PROHIBIDAS):
        raise ErrorRuta(f"'{ruta}' está en una carpeta prohibida para el harness.")
    # Se mira el destino ya resuelto: un symlink a .env con otro nombre también se rechaza.
    if es_archivo_de_secretos(destino.name):
        raise ErrorRuta(
            f"'{ruta}' puede contener secretos (API keys, contraseñas): el harness no lo lee "
            "ni lo escribe."
        )
    return destino


def es_archivo_de_secretos(nombre: str) -> bool:
    """True para .env y .env.* (en cualquier carpeta), salvo .env.example."""
    nombre = nombre.lower()
    if nombre in EXCEPCIONES_SECRETOS:
        return False
    return nombre == ARCHIVO_SECRETOS or nombre.startswith(f"{ARCHIVO_SECRETOS}.")


def validar_escritura(repo: Path, destino: Path, protegidos: Sequence[str] = ()) -> None:
    """Lanza ErrorRuta si el archivo está protegido contra escritura.

    `protegidos` son los globs de archivos_protegidos de harness.yaml.
    """
    relativa = _relativa(repo.resolve(), destino)
    texto = relativa.as_posix().lower()
    if (
        texto in ARCHIVOS_PROTEGIDOS
        or _esta_dentro_de(relativa, CARPETAS_PROTEGIDAS)
        or coincide_alguno(relativa.as_posix(), protegidos)
    ):
        raise ErrorRuta(f"'{relativa}' está protegido: el harness no puede modificarlo.")


def _relativa(repo_resuelto: Path, destino: Path) -> PurePosixPath:
    return PurePosixPath(destino.relative_to(repo_resuelto).as_posix())


def _esta_dentro_de(relativa: PurePosixPath, carpetas: tuple[str, ...]) -> bool:
    # En minúsculas porque en Windows ".GIT" y ".git" son la misma carpeta.
    texto = relativa.as_posix().lower()
    return any(texto == carpeta or texto.startswith(f"{carpeta}/") for carpeta in carpetas)
