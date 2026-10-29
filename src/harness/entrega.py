"""Entrega del trabajo: rama del ticket al empezar; commit, push y pull request al terminar.

Lo hace el harness, no el modelo, y solo si la validación pasó: así nunca se sube código con
tests fallando. Sin harness.yaml no hay validación, así que tampoco hay entrega.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from harness import git, github
from harness.cambios import CambiosRepo
from harness.config_proyecto import ConfigComandos, ConfigGit
from harness.ejecucion import ResultadoEjecucion
from harness.git import ErrorGit
from harness.secretos import es_archivo_de_secretos

# El harness nunca hace push a estas ramas, diga lo que diga harness.yaml.
RAMAS_PROTEGIDAS = ("main", "master")
MAX_ARCHIVOS_EN_ERROR = 5


@dataclass(frozen=True)
class ConfigEntrega:
    """Rama de trabajo y qué se hace al terminar."""

    rama: str
    base: str
    remoto: str
    es_con_push: bool = True
    es_con_pr: bool = True


# Arma el pedido del PR una vez que se sabe el repo de GitHub (sale de la URL del remoto).
ArmarPedidoPr = Callable[[github.RepoGitHub], github.PedidoPullRequest]


@dataclass
class ResultadoEntrega:
    """Qué se entregó: commit, push y PR (o por qué no)."""

    rama: str
    hash_commit: str | None = None
    archivos: list[str] = field(default_factory=list)
    es_pusheado: bool = False
    pull_request: github.PullRequest | None = None
    # Por qué no hubo commit, push o PR (se muestra en consola y queda en el log).
    motivo: str | None = None
    es_rama_borrada: bool = False
    # El push ya se hizo; el PR falló (token sin permisos, sin red...).
    error_pr: str | None = None


def crear_config_entrega(
    config_git: ConfigGit, ticket: str, es_con_push: bool, es_con_pr: bool = True
) -> ConfigEntrega:
    """Rama <prefijo_rama><ticket> desde git.rama_base; el PR necesita el push."""
    return ConfigEntrega(
        rama=f"{config_git.prefijo_rama}{ticket}",
        base=config_git.rama_base,
        remoto=config_git.remoto,
        es_con_push=es_con_push,
        es_con_pr=es_con_push and es_con_pr,
    )


def crear_mensaje_commit(ticket: str, titulo: str) -> str:
    """Una sola línea: "<TICKET>: <título del ticket>"."""
    return f"{ticket}: {titulo}" if titulo else ticket


def preparar_rama(
    repo: Path, config: ConfigEntrega, al_aviso: Callable[[str], None]
) -> None:
    """Comprueba que el repo esté listo y crea la rama del ticket. Lanza ErrorGit si no."""
    _verificar_repo(repo, config)
    if config.es_con_push:
        _verificar_remoto(repo, config, al_aviso)
    elif git.existe_remoto(repo, config.remoto):
        # Sin push el remoto no es obligatorio, pero la base desactualizada importa igual:
        # si no se puede comparar (sin red, sin la rama en el remoto), se avisa y se sigue.
        try:
            _avisar_divergencia(repo, config, al_aviso)
        except ErrorGit as error:
            al_aviso(f"No se pudo comparar {config.base} con el remoto: {error}")
    # Siempre después de comparar: la rama del ticket sale de la base tal como está.
    git.crear_rama(repo, config.rama, config.base)


def entregar(
    repo: Path,
    config: ConfigEntrega,
    resultado: ResultadoEjecucion,
    mensaje_commit: str,
    armar_pedido_pr: ArmarPedidoPr | None = None,
    token_github: str | None = None,
) -> ResultadoEntrega:
    """Commit, push y PR si la validación pasó; si no, deja los cambios sin commitear."""
    entrega = ResultadoEntrega(config.rama)
    if not _es_listo(resultado):
        return _sin_commit(repo, config, entrega, _motivo_no_listo(resultado))
    entrega.archivos = git.preparar_commit(repo)
    if not entrega.archivos:
        return _sin_commit(repo, config, entrega, "El modelo no cambió ningún archivo.")
    _frenar_si_hay_secretos(repo, entrega.archivos)
    entrega.hash_commit = git.commitear(repo, mensaje_commit)
    if not config.es_con_push:
        entrega.motivo = "Sin push (--sin-push): el commit quedó solo en el repo local."
        return entrega
    _pushear(repo, config)
    entrega.es_pusheado = True
    if config.es_con_pr and armar_pedido_pr:
        _abrir_pr(repo, config, entrega, armar_pedido_pr, token_github)
    return entrega


def cerrar_sin_entregar(repo: Path, config: ConfigEntrega) -> ResultadoEntrega:
    """Tras un error o Ctrl+C: borra la rama si quedó vacía; si no, deja los cambios en ella."""
    return _sin_commit(repo, config, ResultadoEntrega(config.rama), "La ejecución no terminó.")


def _verificar_repo(repo: Path, config: ConfigEntrega) -> None:
    if not git.es_repo_git(repo):
        raise ErrorGit(
            "El repo no es un repositorio Git: el harness entrega el código en una rama con "
            "commit y push. Inicialízalo con 'git init' y un primer commit."
        )
    if config.rama in (config.base, *RAMAS_PROTEGIDAS):
        raise ErrorGit(f"La rama de trabajo no puede ser '{config.rama}'.")
    sin_commitear = git.archivos_sin_commitear(repo)
    if sin_commitear:
        raise ErrorGit(
            f"Hay {len(sin_commitear)} archivo(s) sin commitear "
            f"({_listar(sin_commitear)}): commitéalos o guárdalos (git stash) antes de correr "
            "el harness, para que no se mezclen con los cambios del modelo."
        )
    if not git.existe_rama_local(repo, config.base):
        raise ErrorGit(f"No existe la rama base '{config.base}' (git.rama_base en harness.yaml).")
    if not git.identidad_configurada(repo):
        raise ErrorGit(
            "Git no tiene user.name y user.email: configúralos para que el harness pueda "
            "hacer el commit (git config --global user.name / user.email)."
        )
    if git.existe_rama_local(repo, config.rama):
        raise ErrorGit(
            f"La rama '{config.rama}' ya existe. Bórrala (git branch -D {config.rama}) para "
            "empezar el ticket de nuevo."
        )


def _verificar_remoto(
    repo: Path, config: ConfigEntrega, al_aviso: Callable[[str], None]
) -> None:
    if not git.existe_remoto(repo, config.remoto):
        raise ErrorGit(
            f"No existe el remoto '{config.remoto}' (git.remoto en harness.yaml). Usa "
            "--sin-push para trabajar sin remoto."
        )
    _avisar_divergencia(repo, config, al_aviso)
    if git.existe_rama_remota(repo, config.remoto, config.rama):
        raise ErrorGit(
            f"La rama '{config.rama}' ya existe en '{config.remoto}'. Bórrala allá para "
            "empezar el ticket de nuevo."
        )


def _avisar_divergencia(
    repo: Path, config: ConfigEntrega, al_aviso: Callable[[str], None]
) -> None:
    """git fetch de la base y un aviso si la local está adelantada o atrasada."""
    git.traer(repo, config.remoto, config.base)
    divergencia = git.comparar_con_remoto(repo, config.remoto, config.base)
    aviso = _aviso_divergencia(config, divergencia)
    if aviso:
        al_aviso(aviso)


def _aviso_divergencia(config: ConfigEntrega, divergencia: git.Divergencia) -> str | None:
    if not divergencia.adelantados and not divergencia.atrasados:
        return None
    partes = [
        f"La rama {config.base} local está {_commits(divergencia.adelantados)} adelante y "
        f"{_commits(divergencia.atrasados)} atrás de {config.remoto}/{config.base}."
    ]
    if divergencia.adelantados:
        partes.append(
            "Los de adelante entrarían en la rama del ticket y aparecerían en el PR: conviene "
            f"hacer push de {config.base} antes."
        )
    if divergencia.atrasados:
        partes.append(
            "La rama del ticket sale sin los de atrás: conviene actualizarla (git pull) antes."
        )
    return " ".join(partes)


def _es_listo(resultado: ResultadoEjecucion) -> bool:
    return (
        resultado.es_validado is True
        and not resultado.es_limite_alcanzado
        and not resultado.es_plan_rechazado
    )


def _motivo_no_listo(resultado: ResultadoEjecucion) -> str:
    if resultado.es_plan_rechazado:
        return "Plan rechazado: no hay nada que entregar."
    if resultado.es_limite_alcanzado:
        return "Se alcanzó el límite de pasos: sin commit ni push."
    return "La validación no pasó: sin commit ni push."


def _sin_commit(
    repo: Path, config: ConfigEntrega, entrega: ResultadoEntrega, motivo: str
) -> ResultadoEntrega:
    """Sin cambios, la rama no sirve: se vuelve a la base y se borra para poder reintentar."""
    if git.archivos_sin_commitear(repo):
        entrega.motivo = (
            f"{motivo} Los cambios quedaron sin commitear en la rama {config.rama} "
            "(revísalos con git status / git diff)."
        )
        return entrega
    git.volver_y_borrar_rama(repo, config.rama, config.base)
    entrega.es_rama_borrada = True
    entrega.motivo = f"{motivo} Se borró la rama {config.rama}, que quedó vacía."
    return entrega


def _frenar_si_hay_secretos(repo: Path, archivos: list[str]) -> None:
    secretos = [ruta for ruta in archivos if es_archivo_de_secretos(Path(ruta).name)]
    if secretos:
        git.deshacer_preparacion(repo)
        raise ErrorGit(
            f"Entre los cambios hay archivos que pueden tener secretos ({_listar(secretos)}): "
            "no se hace el commit. Agrégalos al .gitignore del proyecto."
        )


def _pushear(repo: Path, config: ConfigEntrega) -> None:
    # Segunda guarda, justo antes del push: nunca a la rama base ni a main/master.
    if config.rama in (config.base, *RAMAS_PROTEGIDAS):
        raise ErrorGit(f"El harness nunca hace push a '{config.rama}'.")
    git.pushear(repo, config.remoto, config.rama)


def _abrir_pr(
    repo: Path,
    config: ConfigEntrega,
    entrega: ResultadoEntrega,
    armar_pedido_pr: ArmarPedidoPr,
    token_github: str | None,
) -> None:
    repo_github = github.repo_desde_remoto(git.url_remoto(repo, config.remoto))
    if repo_github is None:
        entrega.motivo = f"El remoto '{config.remoto}' no es de GitHub: se hizo push, sin PR."
        return
    if not token_github:
        entrega.motivo = "Falta GITHUB_TOKEN en el .env: se hizo push, pero no el pull request."
        return
    try:
        entrega.pull_request = github.crear_pull_request(
            armar_pedido_pr(repo_github), token_github
        )
    except github.ErrorGitHub as error:
        entrega.error_pr = str(error)


def crear_cuerpo_pr(
    resultado: ResultadoEjecucion, cambios: CambiosRepo, comandos: ConfigComandos, modelo: str
) -> str:
    """Descripción del PR en Markdown: resumen del modelo, plan, validación y cambios."""
    resumen = (resultado.texto_final or "").strip() or "_El modelo no dejó un resumen._"
    secciones = [
        f"## Resumen\n\n{resumen}",
        f"## Plan\n\n{_plan_en_markdown(resultado)}",
        f"## Validación\n\n{_validacion_en_markdown(resultado, comandos)}",
        f"## Cambios\n\n{_cambios_en_markdown(cambios)}",
        f"---\n_Generado por harness con `{modelo}`. Revísalo antes de aprobarlo._",
    ]
    return "\n\n".join(secciones)


def _plan_en_markdown(resultado: ResultadoEjecucion) -> str:
    plan = resultado.plan
    if plan is None:
        return "_Se implementó sin plan (--sin-plan)._"
    lineas = [plan.resumen, "", "**Archivos**"]
    lineas += [f"- `{a.ruta}` ({a.accion}): {a.motivo}" for a in plan.archivos]
    lineas += ["", "**Pasos**"]
    lineas += [f"{numero}. {paso}" for numero, paso in enumerate(plan.pasos, start=1)]
    if plan.tests:
        lineas += ["", "**Tests previstos**", *(f"- {test}" for test in plan.tests)]
    return "\n".join(lineas)


def _validacion_en_markdown(resultado: ResultadoEjecucion, comandos: ConfigComandos) -> str:
    validado = f"los tests (`{comandos.tests}`)"
    if comandos.lint:
        validado += f" y el lint (`{comandos.lint}`)"
    return f"Pasaron {validado} en la validación {resultado.validaciones}."


def _cambios_en_markdown(cambios: CambiosRepo) -> str:
    grupos = [
        ("Creados", cambios.creados),
        ("Modificados", cambios.modificados),
        ("Eliminados", cambios.eliminados),
    ]
    lineas = [
        f"- {nombre}: " + ", ".join(f"`{ruta}`" for ruta in rutas)
        for nombre, rutas in grupos
        if rutas
    ]
    return "\n".join(lineas) or "_Sin cambios detectados._"


def _commits(cantidad: int) -> str:
    return "1 commit" if cantidad == 1 else f"{cantidad} commits"


def _listar(rutas: list[str]) -> str:
    visibles = ", ".join(rutas[:MAX_ARCHIVOS_EN_ERROR])
    resto = len(rutas) - MAX_ARCHIVOS_EN_ERROR
    return f"{visibles} y {resto} más" if resto > 0 else visibles
