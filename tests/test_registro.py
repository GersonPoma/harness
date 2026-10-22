import json
from pathlib import Path

from harness.bucle import Paso, ResultadoBucle
from harness.registro import RegistroPasos
from harness.tipos import LlamadaHerramienta


def test_registra_pasos_en_jsonl_sin_secretos(repo: Path) -> None:
    registro = RegistroPasos(repo, "TAREAS-1", secretos=["sk-secreta"])
    registro.registrar_inicio("gemini", "modelo-x")
    argumentos = {"ruta": ".env", "contenido": "K=sk-secreta"}
    llamada = LlamadaHerramienta("c1", "escribir_archivo", argumentos)
    registro.registrar_paso(Paso(1, llamada, "leído sk-secreta", es_error=False))
    registro.registrar_fin(ResultadoBucle("Listo", 2, 20, 10, False))

    assert registro.ruta.parent == repo / ".harness" / "logs"
    assert registro.ruta.name.startswith("TAREAS-1-")
    texto = registro.ruta.read_text(encoding="utf-8")
    assert "sk-secreta" not in texto
    eventos = [json.loads(linea) for linea in texto.splitlines()]
    assert [e["evento"] for e in eventos] == ["inicio", "herramienta", "fin"]
    assert eventos[1]["argumentos"]["contenido"] == "K=***"
