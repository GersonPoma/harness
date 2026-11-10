from pathlib import Path

import pytest

from harness.herramientas import archivos
from harness.herramientas.archivos import (
    AVISO_REESCRIBIR,
    ErrorHerramienta,
    editar_archivo,
    escribir_archivo,
    leer_archivo,
    listar_directorio,
)
from harness.herramientas.rutas import ErrorRuta


def test_leer_archivo(repo: Path) -> None:
    assert leer_archivo(repo, "src/app.py") == "Archivo: src/app.py\nprint('hola')\n"


def test_leer_harness_yaml_esta_permitido(repo: Path) -> None:
    assert leer_archivo(repo, "harness.yaml") == "Archivo: harness.yaml\nversion: 1\n"


def test_leer_archivo_inexistente(repo: Path) -> None:
    with pytest.raises(ErrorHerramienta, match="No existe"):
        leer_archivo(repo, "no_existe.py")


def test_leer_archivo_binario(repo: Path) -> None:
    (repo / "imagen.png").write_bytes(b"\x89PNG\xff\xfe")
    with pytest.raises(ErrorHerramienta, match="UTF-8"):
        leer_archivo(repo, "imagen.png")


def test_leer_archivo_largo_se_recorta(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archivos, "MAX_CARACTERES_LECTURA", 10)
    (repo / "largo.txt").write_text("x" * 50, encoding="utf-8")
    resultado = leer_archivo(repo, "largo.txt")
    assert resultado.startswith("Archivo: largo.txt\n" + "x" * 10 + "\n")
    assert "10 de 50" in resultado


def test_escribir_archivo_crea_carpetas(repo: Path) -> None:
    escribir_archivo(repo, "src/nuevo/modulo.py", "a = 1\nb = 2\n")
    # Se lee en bytes para comprobar que en Windows no se agregan \r.
    assert (repo / "src" / "nuevo" / "modulo.py").read_bytes() == b"a = 1\nb = 2\n"


def test_escribir_archivo_reemplaza(repo: Path) -> None:
    escribir_archivo(repo, "src/app.py", "nuevo")
    assert leer_archivo(repo, "src/app.py") == "Archivo: src/app.py\nnuevo"


def test_escribir_harness_yaml_esta_prohibido(repo: Path) -> None:
    with pytest.raises(ErrorRuta, match="protegido"):
        escribir_archivo(repo, "harness.yaml", "limites: {}")
    assert (repo / "harness.yaml").read_text(encoding="utf-8") == "version: 1\n"


def test_escribir_fuera_del_repo_esta_prohibido(repo: Path) -> None:
    with pytest.raises(ErrorRuta):
        escribir_archivo(repo, "../hackeado.txt", "x")
    assert not (repo.parent / "hackeado.txt").exists()


def test_escribir_en_git_esta_prohibido(repo: Path) -> None:
    with pytest.raises(ErrorRuta):
        escribir_archivo(repo, ".git/hooks/pre-commit", "x")


def test_leer_env_esta_prohibido(repo: Path) -> None:
    (repo / ".env").write_text("API_KEY=sk-real\n", encoding="utf-8")
    with pytest.raises(ErrorRuta, match="puede contener secretos") as error:
        leer_archivo(repo, ".env")
    assert "sk-real" not in str(error.value)


def test_escribir_env_esta_prohibido(repo: Path) -> None:
    for ruta in [".env", ".env.local"]:
        with pytest.raises(ErrorRuta, match="puede contener secretos"):
            escribir_archivo(repo, ruta, "API_KEY=x\n")
        assert not (repo / ruta).exists()


def test_env_example_se_puede_leer_y_escribir(repo: Path) -> None:
    escribir_archivo(repo, ".env.example", "API_KEY=\n")
    assert leer_archivo(repo, ".env.example") == "Archivo: .env.example\nAPI_KEY=\n"


def test_listar_directorio_raiz(repo: Path) -> None:
    # .git se oculta; las carpetas van primero y terminan en '/'.
    assert listar_directorio(repo) == "src/\nharness.yaml"


def test_listar_subcarpeta(repo: Path) -> None:
    assert listar_directorio(repo, "src") == "app.py"


def test_listar_carpeta_vacia(repo: Path) -> None:
    (repo / "vacia").mkdir()
    assert "vacía" in listar_directorio(repo, "vacia")


def test_listar_carpeta_inexistente(repo: Path) -> None:
    with pytest.raises(ErrorHerramienta, match="No existe"):
        listar_directorio(repo, "nada")


def test_listar_fuera_del_repo(repo: Path) -> None:
    with pytest.raises(ErrorRuta):
        listar_directorio(repo, "..")


# --- editar_archivo ----------------------------------------------------------------------

CODIGO = "def a():\n    return 1\n\n\ndef b():\n    return 1\n"


def _codigo(repo: Path, contenido: str = CODIGO, finales: str = "\n") -> Path:
    ruta = repo / "src" / "mod.py"
    ruta.write_bytes(contenido.replace("\n", finales).encode("utf-8"))
    return ruta


def test_editar_reemplaza_un_fragmento_unico(repo: Path) -> None:
    ruta = _codigo(repo)
    escritos: list[str] = []
    resultado = editar_archivo(
        repo, "src/mod.py", "def a():\n    return 1", "def a():\n    return 2",
        al_escribir=escritos.append,
    )
    assert resultado == "Archivo 'src/mod.py' editado (1 reemplazo, línea 1)."
    assert ruta.read_text(encoding="utf-8") == CODIGO.replace("return 1", "return 2", 1)
    assert escritos == ["src/mod.py"]


def test_editar_un_fragmento_repetido_dice_donde_esta(repo: Path) -> None:
    ruta = _codigo(repo)
    with pytest.raises(ErrorHerramienta, match="aparece 2 veces .* líneas 2 y 6: agrega"):
        editar_archivo(repo, "src/mod.py", "return 1", "return 2")
    # No se tocó nada.
    assert ruta.read_text(encoding="utf-8") == CODIGO


def test_editar_todas_las_apariciones(repo: Path) -> None:
    ruta = _codigo(repo)
    resultado = editar_archivo(repo, "src/mod.py", "return 1", "return 2", reemplazar_todas=True)
    assert resultado == "Archivo 'src/mod.py' editado (2 reemplazos, líneas 2 y 6)."
    assert ruta.read_text(encoding="utf-8").count("return 2") == 2


def test_editar_un_fragmento_que_no_aparece(repo: Path) -> None:
    _codigo(repo)
    with pytest.raises(ErrorHerramienta, match="no aparece en 'src/mod.py'. Vuelve a leer"):
        editar_archivo(repo, "src/mod.py", "return 3", "return 4")


def test_editar_respeta_los_finales_de_linea_de_windows(repo: Path) -> None:
    # El modelo manda \n aunque el archivo use \r\n: se busca y se escribe con \r\n.
    ruta = _codigo(repo, finales="\r\n")
    editar_archivo(repo, "src/mod.py", "def b():\n    return 1", "def b():\n    return 3")
    assert ruta.read_bytes() == CODIGO.replace("    return 1\n", "    return 3\n", 2).replace(
        "    return 3\n", "    return 1\n", 1
    ).replace("\n", "\r\n").encode("utf-8")


@pytest.mark.parametrize(
    ("ruta", "actual", "error", "mensaje"),
    [
        ("src/mod.py", "", ErrorHerramienta, "está vacío"),
        ("src/nuevo.py", "x", ErrorHerramienta, "Para crear un archivo usa escribir_archivo"),
        ("harness.yaml", "version", ErrorRuta, "protegido"),
        (".env", "X", ErrorRuta, "secretos"),
        ("../secreto.txt", "fuera", ErrorRuta, "fuera del repo"),
    ],
)
def test_editar_rechaza(repo: Path, ruta: str, actual: str, error: type, mensaje: str) -> None:
    _codigo(repo)
    (repo / ".env").write_text("X=1\n", encoding="utf-8")
    with pytest.raises(error, match=mensaje):
        editar_archivo(repo, ruta, actual, "y")


def test_editar_respeta_los_protegidos_de_harness_yaml(repo: Path) -> None:
    (repo / "migrations").mkdir()
    (repo / "migrations" / "v1.sql").write_text("CREATE TABLE t;\n", encoding="utf-8")
    with pytest.raises(ErrorRuta, match="protegido"):
        editar_archivo(repo, "migrations/v1.sql", "t", "u", protegidos=["migrations/**"])


def test_reescribir_un_archivo_existente_sugiere_editar(repo: Path) -> None:
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    resultado = escribir_archivo(repo, "a.py", "x = 2\n")
    assert resultado == f"Archivo 'a.py' escrito (6 caracteres). {AVISO_REESCRIBIR}"
    assert "editar_archivo" in AVISO_REESCRIBIR


def test_crear_un_archivo_nuevo_no_avisa(repo: Path) -> None:
    assert escribir_archivo(repo, "b.py", "y\n") == "Archivo 'b.py' escrito (2 caracteres)."
