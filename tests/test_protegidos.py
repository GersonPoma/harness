from pathlib import Path

from harness.entorno_docker import ResultadoComando
from harness.herramientas.comandos import EjecutorComandos
from harness.herramientas.protegidos import GuardiaProtegidos
from tests.falsos import EntornoFalso


def _repo_con_tickets(repo: Path) -> Path:
    (repo / "tickets").mkdir()
    (repo / "tickets" / "T-1.md").write_text("original", encoding="utf-8")
    (repo / "tickets" / "T-2.md").write_text("otro", encoding="utf-8")
    return repo


def test_sin_cambios_no_hay_avisos(repo: Path) -> None:
    guardia = GuardiaProtegidos(_repo_con_tickets(repo), ["tickets/**"])
    (repo / "src" / "app.py").write_text("cambio permitido", encoding="utf-8")
    assert guardia.revisar() == []


def test_restaura_archivo_modificado(repo: Path) -> None:
    guardia = GuardiaProtegidos(_repo_con_tickets(repo), ["tickets/**"])
    (repo / "tickets" / "T-1.md").write_text("hackeado", encoding="utf-8")
    avisos = guardia.revisar()
    assert (repo / "tickets" / "T-1.md").read_text(encoding="utf-8") == "original"
    assert len(avisos) == 1 and "tickets/T-1.md" in avisos[0]


def test_restaura_archivo_borrado(repo: Path) -> None:
    guardia = GuardiaProtegidos(_repo_con_tickets(repo), ["tickets/**"])
    (repo / "tickets" / "T-2.md").unlink()
    assert "restauró" in guardia.revisar()[0]
    assert (repo / "tickets" / "T-2.md").read_text(encoding="utf-8") == "otro"


def test_restaura_carpeta_borrada_completa(repo: Path) -> None:
    guardia = GuardiaProtegidos(_repo_con_tickets(repo), ["tickets/**"])
    for archivo in (repo / "tickets").iterdir():
        archivo.unlink()
    (repo / "tickets").rmdir()
    assert len(guardia.revisar()) == 2
    assert (repo / "tickets" / "T-1.md").is_file()


def test_borra_archivo_nuevo_en_ruta_protegida(repo: Path) -> None:
    guardia = GuardiaProtegidos(_repo_con_tickets(repo), ["tickets/**"])
    (repo / "tickets" / "T-99.md").write_text("inventado", encoding="utf-8")
    avisos = guardia.revisar()
    assert not (repo / "tickets" / "T-99.md").exists()
    assert "se eliminó" in avisos[0]


def test_el_aviso_llega_al_resultado_del_comando(repo: Path) -> None:
    """Un comando del modelo (por ejemplo python) modifica un archivo protegido."""
    guardia = GuardiaProtegidos(_repo_con_tickets(repo), ["tickets/**"])

    def modificar_ticket() -> None:
        (repo / "tickets" / "T-1.md").write_text("hackeado", encoding="utf-8")

    entorno = EntornoFalso([ResultadoComando(0, "listo")], efecto=modificar_ticket)
    es_ok, texto = EjecutorComandos(entorno, guardia, 4000, 120).correr("python x.py")

    assert es_ok
    assert texto.startswith("Código de salida: 0\nlisto")
    assert "AVISO" in texto and "tickets/T-1.md" in texto
    assert (repo / "tickets" / "T-1.md").read_text(encoding="utf-8") == "original"
