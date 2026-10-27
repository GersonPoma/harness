"""Comandos de Git sobre el repo objetivo, con la CLI de git del host.

Los corre el harness, nunca el modelo: dentro del contenedor .git es de solo lectura. El push
usa las credenciales de Git del usuario (las mismas que usa en su terminal).
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_LOCAL_SEGUNDOS = 30
# fetch, ls-remote y push van por la red.
TIMEOUT_RED_SEGUNDOS = 120
# Lo que escribe el harness en el repo: nunca va al commit ni cuenta como "árbol sucio",
# aunque el .gitignore del proyecto no lo excluya.
EXCLUIDOS = (":(exclude).harness/logs", ":(exclude).harness/sessions")
# Sin terminal donde preguntar: si git necesita usuario y contraseña, que falle en vez de
# quedarse esperando para siempre.
ENTORNO_SIN_PREGUNTAS = {"GIT_TERMINAL_PROMPT": "0"}


class ErrorGit(Exception):
    """Un comando de git falló o el repo no está en condiciones de usarse."""


@dataclass(frozen=True)
class Divergencia:
    """Commits de la rama local que no están en el remoto, y al revés."""

    adelantados: int
    atrasados: int


def ejecutar_git(repo: Path, *argumentos: str, timeout: int = TIMEOUT_LOCAL_SEGUNDOS) -> str:
    """Corre git en el repo y devuelve su salida; lanza ErrorGit si falla."""
    try:
        proceso = subprocess.run(
            ["git", "-C", str(repo), *argumentos],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, **ENTORNO_SIN_PREGUNTAS},
        )
    except FileNotFoundError as error:
        raise ErrorGit("No se encontró el comando 'git': instala Git.") from error
    except subprocess.TimeoutExpired as error:
        raise ErrorGit(f"'git {argumentos[0]}' tardó más de {timeout} s.") from error
    if proceso.returncode != 0:
        detalle = (proceso.stderr or proceso.stdout).strip()
        raise ErrorGit(f"'git {' '.join(argumentos)}' falló: {detalle}")
    # Solo a la derecha: en 'git status --porcelain' el espacio inicial es parte del estado.
    return proceso.stdout.rstrip()


def es_repo_git(repo: Path) -> bool:
    """True si la carpeta está dentro de un repositorio Git."""
    try:
        return ejecutar_git(repo, "rev-parse", "--is-inside-work-tree") == "true"
    except ErrorGit:
        return False


def archivos_sin_commitear(repo: Path) -> list[str]:
    """Cambios del árbol de trabajo (sin lo ignorado ni los logs del harness)."""
    salida = ejecutar_git(repo, "status", "--porcelain", "--", ".", *EXCLUIDOS)
    return [linea[3:] for linea in salida.splitlines() if linea.strip()]


def existe_rama_local(repo: Path, rama: str) -> bool:
    """True si la rama existe en el repo local."""
    try:
        ejecutar_git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{rama}")
    except ErrorGit:
        return False
    return True


def existe_remoto(repo: Path, remoto: str) -> bool:
    """True si el remoto está configurado."""
    return remoto in ejecutar_git(repo, "remote").splitlines()


def url_remoto(repo: Path, remoto: str) -> str:
    """URL del remoto (https o ssh)."""
    return ejecutar_git(repo, "remote", "get-url", remoto)


def existe_rama_remota(repo: Path, remoto: str, rama: str) -> bool:
    """True si la rama ya está en el remoto (consulta la red)."""
    salida = ejecutar_git(
        repo, "ls-remote", "--heads", remoto, rama, timeout=TIMEOUT_RED_SEGUNDOS
    )
    return bool(salida)


def traer(repo: Path, remoto: str, rama: str) -> None:
    """git fetch de una rama, para comparar la local con la del remoto."""
    ejecutar_git(repo, "fetch", remoto, rama, timeout=TIMEOUT_RED_SEGUNDOS)


def comparar_con_remoto(repo: Path, remoto: str, rama: str) -> Divergencia:
    """Cuántos commits está la rama local adelantada y atrasada respecto del remoto."""
    salida = ejecutar_git(
        repo, "rev-list", "--left-right", "--count", f"{rama}...{remoto}/{rama}"
    )
    adelantados, atrasados = (int(numero) for numero in salida.split())
    return Divergencia(adelantados, atrasados)


def identidad_configurada(repo: Path) -> bool:
    """True si git tiene user.name y user.email (sin ellos no se puede commitear)."""
    try:
        return bool(
            ejecutar_git(repo, "config", "user.name") and ejecutar_git(repo, "config", "user.email")
        )
    except ErrorGit:
        return False


def crear_rama(repo: Path, rama: str, desde: str) -> None:
    """Crea la rama desde `desde` y se cambia a ella."""
    ejecutar_git(repo, "switch", "-c", rama, desde)


def volver_y_borrar_rama(repo: Path, rama: str, base: str) -> None:
    """Vuelve a la rama base y borra la rama de trabajo (solo si no tiene commits propios)."""
    ejecutar_git(repo, "switch", base)
    ejecutar_git(repo, "branch", "-d", rama)


def preparar_commit(repo: Path) -> list[str]:
    """git add de todo lo que cambió (sin los logs del harness); devuelve lo agregado."""
    ejecutar_git(repo, "add", "-A", "--", ".", *EXCLUIDOS)
    salida = ejecutar_git(repo, "diff", "--cached", "--name-only", "-z")
    return [ruta for ruta in salida.split("\0") if ruta]


def deshacer_preparacion(repo: Path) -> None:
    """Saca todo del índice sin tocar los archivos (git reset)."""
    ejecutar_git(repo, "reset", "--quiet")


def commitear(repo: Path, mensaje: str) -> str:
    """Hace el commit con la identidad de git del usuario y devuelve su hash corto."""
    ejecutar_git(repo, "commit", "--quiet", "-m", mensaje)
    return ejecutar_git(repo, "rev-parse", "--short", "HEAD")


def pushear(repo: Path, remoto: str, rama: str) -> None:
    """git push -u de la rama de trabajo."""
    ejecutar_git(repo, "push", "--quiet", "-u", remoto, rama, timeout=TIMEOUT_RED_SEGUNDOS)
