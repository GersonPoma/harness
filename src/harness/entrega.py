"""Entrega del trabajo: rama del ticket al empezar; commit y push al terminar.

Lo hace el harness, no el modelo, y solo si la validación pasó: así nunca se sube código con
tests fallando. Sin harness.yaml no hay validación, así que tampoco hay entrega.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from harness import git
from harness.config_proyecto import ConfigGit
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


@dataclass
class ResultadoEntrega:
    """Qué se entregó: commit y push (o por qué no)."""

    rama: str
    hash_commit: str | None = None
    archivos: list[str] = field(default_factory=list)
    es_pusheado: bool = False
    # Por qué no hubo commit o push (se muestra en consola y queda en el log).
    motivo: str | None = None
    es_rama_borrada: bool = False


def crear_config_entrega(config_git: ConfigGit, ticket: str, es_con_push: bool) -> ConfigEntrega:
    """Rama <prefijo_rama><ticket> desde git.rama_base, con o sin push."""
    return ConfigEntrega(
        rama=f"{config_git.prefijo_rama}{ticket}",
        base=config_git.rama_base,
        remoto=config_git.remoto,
        es_con_push=es_con_push,
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
    git.crear_rama(repo, config.rama, config.base)


def entregar(
    repo: Path,
    config: ConfigEntrega,
    resultado: ResultadoEjecucion,
    mensaje_commit: str,
) -> ResultadoEntrega:
    """Commit y push si la validación pasó; si no, deja los cambios sin commitear."""
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
    git.traer(repo, config.remoto, config.base)
    divergencia = git.comparar_con_remoto(repo, config.remoto, config.base)
    for aviso in _avisos_divergencia(config, divergencia):
        al_aviso(aviso)
    if git.existe_rama_remota(repo, config.remoto, config.rama):
        raise ErrorGit(
            f"La rama '{config.rama}' ya existe en '{config.remoto}'. Bórrala allá para "
            "empezar el ticket de nuevo."
        )


def _avisos_divergencia(config: ConfigEntrega, divergencia: git.Divergencia) -> list[str]:
    base_remota = f"{config.remoto}/{config.base}"
    avisos = []
    if divergencia.adelantados:
        avisos.append(
            f"La rama {config.base} local tiene {_commits(divergencia.adelantados)} sin subir a "
            f"{base_remota}: entrarían en la rama del ticket y aparecerían en el PR. Conviene "
            f"hacer push de {config.base} antes."
        )
    if divergencia.atrasados:
        avisos.append(
            f"La rama {config.base} local está {_commits(divergencia.atrasados)} atrasada "
            f"respecto de {base_remota}: la rama del ticket sale sin esos cambios. Conviene "
            f"actualizarla (git pull) antes."
        )
    return avisos


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


def _commits(cantidad: int) -> str:
    return "1 commit" if cantidad == 1 else f"{cantidad} commits"


def _listar(rutas: list[str]) -> str:
    visibles = ", ".join(rutas[:MAX_ARCHIVOS_EN_ERROR])
    resto = len(rutas) - MAX_ARCHIVOS_EN_ERROR
    return f"{visibles} y {resto} más" if resto > 0 else visibles
