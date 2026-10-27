import json
from pathlib import Path

from harness.bucle import Paso
from harness.ejecucion import ResultadoEjecucion, Validacion
from harness.registro import RegistroPasos
from harness.tipos import LlamadaHerramienta


def test_registra_pasos_en_jsonl_sin_secretos(repo: Path) -> None:
    registro = RegistroPasos(repo, "TAREAS-1", secretos=["sk-secreta"])
    registro.registrar_inicio("gemini", "modelo-x")
    argumentos = {"ruta": ".env", "contenido": "K=sk-secreta"}
    llamada = LlamadaHerramienta("c1", "escribir_archivo", argumentos)
    registro.registrar_paso(Paso(1, llamada, "leído sk-secreta", es_error=False))
    registro.registrar_fin(ResultadoEjecucion("Listo", 2, 20, 10, False, True, 1))

    assert registro.ruta.parent == repo / ".harness" / "logs"
    assert registro.ruta.name.startswith("TAREAS-1-")
    texto = registro.ruta.read_text(encoding="utf-8")
    assert "sk-secreta" not in texto
    eventos = [json.loads(linea) for linea in texto.splitlines()]
    assert [e["evento"] for e in eventos] == ["inicio", "herramienta", "fin"]
    assert eventos[1]["argumentos"]["contenido"] == "K=****"


def test_el_log_guarda_el_resultado_completo_que_recibio_el_modelo(repo: Path) -> None:
    # Como una salida de Maven: mucho ruido al principio y el error al final.
    resultado = "Código de salida: 1\n" + "INFO ruido\n" * 600 + "expected: 10\n but was: 9"
    informe = "## Tests (mvn -B -q test)\n" + resultado
    registro = RegistroPasos(repo, "T", secretos=[])
    llamada = LlamadaHerramienta("c1", "ejecutar_tests", {})
    registro.registrar_paso(Paso(19, llamada, resultado, es_error=False))
    registro.registrar_validacion(Validacion(1, False, informe))

    lineas = registro.ruta.read_text(encoding="utf-8").splitlines()
    paso, validacion = [json.loads(linea) for linea in lineas]
    assert len(resultado) > 6000
    assert paso["resultado"] == resultado
    assert validacion["informe"] == informe


def test_oculta_secretos_con_comillas_o_barras(repo: Path) -> None:
    registro = RegistroPasos(repo, "T", secretos=['cl"a\\ve'])
    registro.registrar_error('falló con cl"a\\ve')
    texto = registro.ruta.read_text(encoding="utf-8")
    assert "cl" not in texto
    assert json.loads(texto)["mensaje"] == "falló con ****"
