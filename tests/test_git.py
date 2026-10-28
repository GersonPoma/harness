from pathlib import Path

import pytest

from harness.git import ErrorGit, ejecutar_git
from tests.git_prueba import git, iniciar_repo, requiere_git

pytestmark = requiere_git


@pytest.fixture
def repo_con_crlf(tmp_path: Path) -> Path:
    """Repo con core.autocrlf=true y un archivo con LF: git avisa de LF/CRLF al agregarlo."""
    repo = tmp_path / "proyecto"
    iniciar_repo(repo)
    git(repo, "config", "core.autocrlf", "true")
    # write_bytes: en Windows write_text escribe CRLF y git ya no avisaría.
    (repo / ".gitignore").write_bytes(b"ignorado/\n")
    (repo / "app.py").write_bytes(b"x = 1\n")
    return repo


def test_los_avisos_de_git_no_son_un_error(repo_con_crlf: Path) -> None:
    ejecutar_git(repo_con_crlf, "add", "-A")
    assert "app.py" in ejecutar_git(repo_con_crlf, "diff", "--cached", "--name-only")


def test_el_error_no_incluye_los_avisos(repo_con_crlf: Path) -> None:
    # Nombrar una ruta ignorada en git add falla, y los avisos de LF/CRLF salen igual.
    (repo_con_crlf / "ignorado").mkdir()
    (repo_con_crlf / "ignorado" / "log.txt").write_text("1\n", encoding="utf-8")
    with pytest.raises(ErrorGit) as error:
        ejecutar_git(repo_con_crlf, "add", "-A", "--", ".", "ignorado")
    mensaje = str(error.value)
    assert "ignored by one of your .gitignore files" in mensaje
    assert "warning:" not in mensaje and "CRLF" not in mensaje
