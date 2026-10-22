from pathlib import Path

from harness.secretos import buscar_archivos_de_secretos


def _crear(repo: Path, *rutas: str) -> None:
    for ruta in rutas:
        (repo / ruta).parent.mkdir(parents=True, exist_ok=True)
        (repo / ruta).write_text("API_KEY=real\n", encoding="utf-8")


def test_busca_env_en_subcarpetas_saltando_las_ignoradas(repo: Path) -> None:
    _crear(
        repo,
        ".env", "backend/.env.local", "backend/config/.ENV.production",
        ".env.example", ".envrc",
        ".venv/.env", "node_modules/paquete/.env", ".git/.env",
    )
    assert buscar_archivos_de_secretos(repo) == [
        ".env", "backend/.env.local", "backend/config/.ENV.production",
    ]


def test_carpeta_llamada_env_no_cuenta(repo: Path) -> None:
    (repo / ".env").mkdir()
    assert buscar_archivos_de_secretos(repo) == []
