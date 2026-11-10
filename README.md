# harness

Harness de IA por consola para generar código a partir de un ticket. Recibe un ticket de Jira
(o un `.md` local), entiende el proyecto, planifica, implementa con un modelo de IA por API,
corre los tests en Docker, se autocorrige si fallan, revisa el diff y termina con commit, push
a una rama y un pull request en borrador.

El bucle de herramientas está hecho a mano (sin frameworks de agentes) y no está atado a
ningún proveedor: Gemini, Groq, OpenRouter, OpenAI, Anthropic o cualquier API compatible con
OpenAI.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python si hace falta).
- Docker (Docker Desktop en Windows y macOS): el código generado se ejecuta solo dentro de
  contenedores.
- Git, con tus credenciales para hacer push al repo del proyecto.
- Una API key de algún proveedor de modelos. Para el PR, un token de GitHub fine-grained
  (Contents: Read-only, Pull requests: Read and write); para tickets de Jira Cloud, un API
  token de Atlassian.

## Instalación

```powershell
uv tool install git+https://github.com/GersonPoma/harness
harness version
```

Para una versión fija: `uv tool install git+https://github.com/GersonPoma/harness@v0.1.0`.

## Primer uso

```powershell
harness config                  # proveedor, modelo, API key y (si quieres) GitHub y Jira
harness perfil probar           # comprueba la key y que el modelo llame herramientas
cd C:\ruta\a\tu-proyecto
harness init --verificar        # genera AGENTS.md y harness.yaml, y prueba la instalación
harness run TICKET-123          # o: harness run tickets\TICKET-1.md
```

`harness config` guarda todo en `~/.harness/config.toml` (en Windows,
`C:\Users\<usuario>\.harness\config.toml`), fuera de cualquier repo. Sin terminal
interactiva: `harness config --credencial NOMBRE` (el valor por stdin) y
`harness perfil agregar <nombre> --proveedor X --modelo Y`.

## Comandos

| Comando | Qué hace |
|---|---|
| `harness run <TICKET>` | Procesa un ticket: rama, plan, código, tests, revisión, commit, push y PR |
| `harness resume <TICKET>` | Continúa una ejecución que se cortó, desde donde quedó |
| `harness ticket <TICKET>` | Muestra el ticket tal como lo recibe el modelo |
| `harness init` | Genera `AGENTS.md` y `harness.yaml` en el repo (`--verificar` los prueba) |
| `harness limpiar` | Borra lo que dejó el harness en Docker (`--cache`, `--imagenes`) |
| `harness config` | Modelo y credenciales (`--mostrar`, `--credencial`, `--importar-env`) |
| `harness perfiles` | Lista los perfiles de modelo y marca el predeterminado |
| `harness perfil usar\|agregar\|editar\|borrar\|probar` | Maneja los perfiles |
| `harness version` | La versión instalada |

Opciones útiles de `run`: `--perfil`, `--proveedor`/`--modelo`, `--revisor` (otro modelo
para revisar el diff), `--aprobar-plan`, `--sin-push`, `--sin-pr`, `--max-pasos`.

## Configuración

- **Por usuario** (`~/.harness/`): `config.toml` con los perfiles de modelo, el respaldo (si
  un proveedor se cae, la conversación sigue con el siguiente), el revisor y las
  credenciales; y `mcp.json` con los servidores MCP de GitHub y Jira (`harness config` lo
  crea a partir del ejemplo). El modelo se elige así: `--proveedor/--modelo`, `--perfil`, las
  variables de entorno (`HARNESS_PROVEEDOR`...) y el perfil predeterminado, en ese orden.
- **Por proyecto** (en la raíz del repo, se suben a Git): `AGENTS.md` con las convenciones que
  sigue el modelo y `harness.yaml` con la imagen de Docker, los comandos de tests y lint y los
  límites. Ver [docs/harness-yaml.md](docs/harness-yaml.md).

## Seguridad

- El código generado corre solo en Docker, con timeout y una lista de comandos permitidos; las
  bases de datos van en contenedores propios de cada ejecución.
- Push solo a la rama del ticket (`feature/<TICKET>`), nunca a `main`; el PR es borrador.
- Las credenciales nunca van en `harness.yaml` ni en el repo; se ocultan en el log, en lo que
  ve el modelo y en el PR. El modelo no puede leer `.env`, `.env.*` ni `.envrc`, y dentro del
  contenedor se ven vacíos.

## Desarrollo

```powershell
git clone https://github.com/GersonPoma/harness
cd harness
uv sync
uv run harness ...              # o: uv tool install -e .
uv run pytest -q                # los tests con Docker real: uv run pytest -m docker
```

En desarrollo se puede usar un `.env` en la raíz del repo del harness (ver `.env.example`);
`harness config --importar-env` lo pasa a `~/.harness/config.toml`. El plan completo del
proyecto está en [docs/plan-harness-ia.md](docs/plan-harness-ia.md).
