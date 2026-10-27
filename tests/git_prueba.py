"""Repos Git reales para los tests, con un remoto local (git init --bare) en vez de GitHub."""

import shutil
import subprocess
from pathlib import Path

import pytest

RAMA_BASE = "main"
requiere_git = pytest.mark.skipif(shutil.which("git") is None, reason="Git no está instalado")


def git(repo: Path, *argumentos: str) -> str:
    """Corre git en el repo y devuelve su salida."""
    proceso = subprocess.run(
        ["git", "-C", str(repo), *argumentos],
        capture_output=True, encoding="utf-8", check=True,
    )
    return proceso.stdout.strip()


def iniciar_repo(repo: Path) -> None:
    """git init con rama main, identidad local y un commit con todo lo que haya."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "--quiet", "--initial-branch", RAMA_BASE)
    git(repo, "config", "user.name", "Prueba")
    git(repo, "config", "user.email", "prueba@example.com")
    # Sin esto, Git en Windows convierte los saltos de línea y los archivos parecen cambiados.
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "--allow-empty", "-m", "inicio")


def agregar_remoto(repo: Path, carpeta: Path, nombre: str = "origin") -> Path:
    """Crea un remoto vacío (bare) en `carpeta`, lo configura y sube main."""
    git(carpeta.parent, "init", "--quiet", "--bare", "--initial-branch", RAMA_BASE, str(carpeta))
    git(repo, "remote", "add", nombre, str(carpeta))
    git(repo, "push", "--quiet", "-u", nombre, RAMA_BASE)
    return carpeta


def ramas_remotas(remoto: Path) -> list[str]:
    """Ramas que llegaron al remoto."""
    salida = git(remoto, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return salida.splitlines()
