import pytest

from harness.globs import coincide_alguno


@pytest.mark.parametrize(
    ("ruta", "patron", "coincide"),
    [
        ("tickets/TAREAS-1.md", "tickets/**", True),
        ("tickets/viejos/T.md", "tickets/**", True),
        ("src/tickets/T.md", "tickets/**", False),
        ("requirements.txt", "requirements*.txt", True),
        ("requirements-dev.txt", "requirements*.txt", True),
        ("sub/requirements.txt", "requirements*.txt", False),
        ("pyproject.toml", "pyproject.toml", True),
        ("a/b/c/x.sql", "**/*.sql", True),
        ("x.sql", "**/*.sql", True),
        ("migrations/0001.py", "migrations/*.py", True),
        ("migrations/sub/0001.py", "migrations/*.py", False),
        ("Docs/guia.md", "docs/**", True),
        ("a.py", "?.py", True),
        ("ab.py", "?.py", False),
        ("pyproject.toml", "pyproject.tom", False),
        ("archivo[1].txt", "archivo[1].txt", True),
    ],
)
def test_coincide(ruta: str, patron: str, coincide: bool) -> None:
    assert coincide_alguno(ruta, [patron]) is coincide


def test_sin_patrones_no_coincide() -> None:
    assert not coincide_alguno("a.py", [])
