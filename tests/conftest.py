from pathlib import Path

import pytest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo objetivo de prueba con algunos archivos."""
    raiz = tmp_path / "repo"
    (raiz / "src").mkdir(parents=True)
    (raiz / "src" / "app.py").write_text("print('hola')\n", encoding="utf-8")
    (raiz / "harness.yaml").write_text("version: 1\n", encoding="utf-8")
    (raiz / ".git").mkdir()
    (raiz / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / "secreto.txt").write_text("fuera del repo", encoding="utf-8")
    return raiz
