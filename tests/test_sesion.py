import json
from datetime import datetime
from pathlib import Path

import pytest

from harness import sesion as modulo_sesion
from harness.sesion import (
    ESTADO_IMPLEMENTACION,
    MOTIVO_CTRL_C,
    ErrorSesion,
    Sesion,
    SesionEnCurso,
    armar_reanudacion,
    cargar_sesion,
    descartar_sesion,
    guardar_sesion,
    quitar_llamadas_sin_respuesta,
    ruta_sesion,
)
from harness.tipos import LlamadaHerramienta, Mensaje, Rol

FIRMA = {"extra_content": {"google": {"thought_signature": "EmAK+firma/=="}}}
SECRETO = "ghp_secreto0123456789"


def _llamada(id_llamada: str = "c1") -> LlamadaHerramienta:
    return LlamadaHerramienta(
        id_llamada, "leer_archivo", {"ruta": "ñandú.py"}, datos_proveedor=FIRMA,
        origen="gemini/modelo-x",
    )


def _sesion(**cambios) -> Sesion:
    datos = {
        "ticket": {"clave": "TAREAS-3", "titulo": "Completar", "fuente": "jira", "url": "u"},
        "estado": ESTADO_IMPLEMENTACION,
        "proveedor": "gemini/modelo-x",
        "modelos_usados": ["gemini/modelo-x"],
        "mensajes": [
            Mensaje(Rol.SISTEMA, "sis"),
            Mensaje(Rol.USUARIO, "Implementa el ticket"),
            Mensaje(Rol.ASISTENTE, None, [_llamada()]),
            Mensaje(Rol.HERRAMIENTA, "print('hola')", id_llamada="c1"),
        ],
        "pasos": 4,
        "plan": {
            "resumen": "r", "pasos": ["a"], "tests": [],
            "archivos": [{"ruta": "a.py", "accion": "modificar", "motivo": "m"}],
        },
        "rama": {"nombre": "feature/TAREAS-3", "base": "main", "head_al_cortar": "abc"},
        "repo_al_empezar": {"a.py": "h1"},
    }
    return Sesion(**{**datos, **cambios})


def test_ida_y_vuelta(tmp_path: Path) -> None:
    original = _sesion()
    ruta = guardar_sesion(tmp_path, original, [])
    assert ruta == tmp_path / ".harness" / "sessions" / "TAREAS-3.json"
    assert cargar_sesion(tmp_path, "TAREAS-3") == original


def test_no_hay_sesion(tmp_path: Path) -> None:
    assert cargar_sesion(tmp_path, "TAREAS-9") is None


def test_los_secretos_quedan_ocultos_y_el_json_es_valido(tmp_path: Path) -> None:
    sesion = _sesion(mensajes=[Mensaje(Rol.HERRAMIENTA, f'token "{SECRETO}"', id_llamada="c1")])
    ruta = guardar_sesion(tmp_path, sesion, [SECRETO])
    texto = ruta.read_text(encoding="utf-8")
    assert SECRETO not in texto
    assert json.loads(texto)["mensajes"][0]["contenido"] == 'token "****"'


def test_la_escritura_es_atomica(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guardar_sesion(tmp_path, _sesion(), [])

    def fallar(*_args: object) -> None:
        raise KeyboardInterrupt

    # Un corte justo al reemplazar: queda la sesión anterior entera y ningún temporal.
    monkeypatch.setattr(modulo_sesion.os, "replace", fallar)
    with pytest.raises(KeyboardInterrupt):
        guardar_sesion(tmp_path, _sesion(pasos=9), [])
    assert cargar_sesion(tmp_path, "TAREAS-3").pasos == 4
    assert [p.name for p in ruta_sesion(tmp_path, "x").parent.iterdir()] == ["TAREAS-3.json"]


@pytest.mark.parametrize("contenido", ["{roto", '{"version": 99, "mensajes": []}'])
def test_sesion_rota_o_de_otra_version(tmp_path: Path, contenido: str) -> None:
    ruta = ruta_sesion(tmp_path, "TAREAS-3")
    ruta.parent.mkdir(parents=True)
    ruta.write_text(contenido, encoding="utf-8")
    with pytest.raises(ErrorSesion):
        cargar_sesion(tmp_path, "TAREAS-3")


def test_descartar_renombra_sin_borrar(tmp_path: Path) -> None:
    guardar_sesion(tmp_path, _sesion(), [])
    destino = descartar_sesion(tmp_path, "TAREAS-3", datetime(2026, 11, 5, 9, 30, 0))
    assert destino.name == "TAREAS-3.descartada-20261105-093000.json"
    assert destino.is_file() and cargar_sesion(tmp_path, "TAREAS-3") is None


def test_quita_la_respuesta_con_llamadas_sin_resultado() -> None:
    base = [Mensaje(Rol.SISTEMA, "s"), Mensaje(Rol.USUARIO, "u")]
    completa = [*base, Mensaje(Rol.ASISTENTE, None, [_llamada()]),
                Mensaje(Rol.HERRAMIENTA, "r", id_llamada="c1")]
    assert quitar_llamadas_sin_respuesta(completa) == completa
    # Dos llamadas y un solo resultado: Ctrl+C a mitad del paso.
    a_medias = [*base, Mensaje(Rol.ASISTENTE, None, [_llamada("c1"), _llamada("c2")]),
                Mensaje(Rol.HERRAMIENTA, "r", id_llamada="c1")]
    assert quitar_llamadas_sin_respuesta(a_medias) == base
    sin_resultados = [*base, Mensaje(Rol.ASISTENTE, None, [_llamada()])]
    assert quitar_llamadas_sin_respuesta(sin_resultados) == base
    assert quitar_llamadas_sin_respuesta(base) == base


def test_sesion_en_curso_sigue_la_conversacion_viva(tmp_path: Path) -> None:
    mensajes = [Mensaje(Rol.USUARIO, "u")]
    en_curso = SesionEnCurso(tmp_path, _sesion(mensajes=[]), [])
    en_curso.usar_mensajes(mensajes, {"a.py": "h"})
    mensajes.append(Mensaje(Rol.ASISTENTE, "listo"))
    en_curso.paso(7)
    guardada = cargar_sesion(tmp_path, "TAREAS-3")
    assert [m.contenido for m in guardada.mensajes] == ["u", "listo"]
    assert guardada.pasos == 7 and guardada.repo_al_empezar == {"a.py": "h"}


def test_cortar_guarda_el_motivo_y_la_foto_del_repo(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    en_curso = SesionEnCurso(tmp_path, _sesion(), [])
    en_curso.cortar(MOTIVO_CTRL_C, "cancelada")
    guardada = cargar_sesion(tmp_path, "TAREAS-3")
    assert (guardada.motivo_corte, guardada.detalle_corte) == (MOTIVO_CTRL_C, "cancelada")
    assert "app.py" in guardada.repo_al_cortar


def test_si_no_se_puede_guardar_avisa_una_vez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disco_lleno(*_args: object) -> None:
        raise OSError("No queda espacio")

    monkeypatch.setattr(modulo_sesion, "guardar_sesion", disco_lleno)
    avisos: list[str] = []
    en_curso = SesionEnCurso(tmp_path, _sesion(), [], avisos.append)
    en_curso.guardar()
    en_curso.paso(5)
    assert avisos == ["No se pudo guardar la sesión para harness resume: No queda espacio"]


def test_armar_reanudacion_tras_un_ctrl_c_a_mitad_de_paso() -> None:
    a_medias = Mensaje(Rol.ASISTENTE, None, [_llamada("c2")])
    sesion = _sesion(validaciones=1, tokens_entrada=50)
    sesion.mensajes.append(a_medias)
    reanudacion = armar_reanudacion(sesion, "se canceló con Ctrl+C", ["app.py"])

    # Sin el system prompt (se regenera) y sin la llamada que quedó sin resultado.
    assert reanudacion.mensajes[0].contenido == "Implementa el ticket"
    assert a_medias not in reanudacion.mensajes
    # El último es un resultado de herramienta: la nota va dentro, no como mensaje aparte.
    ultimo = reanudacion.mensajes[-1]
    assert ultimo.rol == Rol.HERRAMIENTA and ultimo.id_llamada == "c1"
    assert ultimo.contenido.startswith("print('hola')\n\nLa ejecución se cortó (se canceló")
    assert "app.py" in ultimo.contenido
    continuacion = reanudacion.continuacion
    assert (continuacion.pasos, continuacion.validaciones, continuacion.tokens_entrada) == (
        4, 1, 50,
    )
    assert continuacion.plan.resumen == "r"


def test_armar_reanudacion_despues_de_un_mensaje_del_asistente() -> None:
    sesion = _sesion(plan=None)
    sesion.mensajes.append(Mensaje(Rol.ASISTENTE, "Terminé."))
    reanudacion = armar_reanudacion(sesion, "los tests no pasaron", [])
    assert reanudacion.mensajes[-1].rol == Rol.USUARIO
    assert reanudacion.continuacion.plan is None
