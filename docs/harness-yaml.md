# Especificación de `harness.yaml` (versión 1)

`harness.yaml` es el archivo de configuración que el harness lee de la **raíz del repo
objetivo** (el proyecto sobre el que trabaja, no el repo del harness). Le dice al harness cómo
instalar el proyecto, cómo comprobar que el código está bien y qué límites respetar.

- Lo lee **el harness**, no el modelo. Lo que el modelo necesita saber (arquitectura,
  convenciones) va en `AGENTS.md`.
- Se sube a Git junto al código, así que **no lleva secretos**: solo valores de prueba o
  referencias `${VARIABLE}` (ver [Variables de entorno](#variables_entorno)).
- El harness lo valida completo **al arrancar**, antes de crear contenedores o llamar al
  modelo. Si tiene un error, se detiene sin gastar tokens.

---

## Ejemplo mínimo

Lo único obligatorio: versión, nombre, imagen y comando de tests.

```yaml
version: 1
proyecto: tareas-api

docker:
  imagen: python:3.12-slim

comandos:
  tests: pytest -q
```

## Ejemplo completo

```yaml
version: 1
proyecto: mi-api
requiere_agents_md: false  # true = harness run se detiene si falta AGENTS.md

docker:
  imagen: python:3.11-slim
  directorio_trabajo: /app
  instalar:
    - pip install -r requirements.txt
    - pip install -r requirements-dev.txt
  reinstalar_si_cambia:
    - requirements*.txt
  cache: true                       # caché de dependencias entre ejecuciones (por defecto)

comandos:
  tests: pytest -q
  lint: ruff check .

comandos_permitidos:
  - pytest
  - python
  - ls
  - cat

servicios:
  - nombre: db
    imagen: postgres:16
    variables:
      POSTGRES_PASSWORD: test
      POSTGRES_DB: app_test
    listo_cuando: pg_isready -h 127.0.0.1 -U postgres
    timeout_listo_segundos: 60

variables_entorno:
  DATABASE_URL: postgresql://postgres:test@db:5432/app_test
  SECRET_KEY: clave-de-prueba
  API_EXTERNA_KEY: ${MI_API_EXTERNA_KEY}

git:
  rama_base: main
  prefijo_rama: feature/
  remoto: origin

limites:
  max_pasos: 30
  max_pasos_plan: 10                # cada ronda de plan; no descuenta de max_pasos
  max_intentos_validacion: 3
  timeout_comando_segundos: 120
  max_caracteres_salida: 4000

revision:
  activa: true
  max_vueltas: 2
  max_pasos: 6

archivos_protegidos:
  - migrations/**
  - docs/**
```

---

## Referencia de claves

### Raíz

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `version` | entero | sí | | Versión del formato. Hoy solo existe `1`. Permite cambiar el formato en el futuro sin romper proyectos viejos. |
| `proyecto` | texto | sí | | Nombre del proyecto. Solo minúsculas, números y guiones (`[a-z0-9-]+`). Se usa para nombrar la red y los contenedores: `harness-<proyecto>-<ticket>-<id>`, donde `<id>` es propio de cada ejecución (6 caracteres), para que dos ejecuciones del mismo proyecto nunca compartan nombre. |
| `docker` | objeto | sí | | Contenedor donde corre el proyecto. Ver [`docker`](#docker). |
| `comandos` | objeto | sí | | Cómo se valida el código. Ver [`comandos`](#comandos). |
| `comandos_permitidos` | lista de texto | no | `[]` | Ver [`comandos_permitidos`](#comandos_permitidos). |
| `servicios` | lista | no | `[]` | Ver [`servicios`](#servicios). |
| `variables_entorno` | mapa texto→texto | no | `{}` | Ver [`variables_entorno`](#variables_entorno). |
| `git` | objeto | no | (valores por defecto) | Ver [`git`](#git). |
| `limites` | objeto | no | (valores por defecto) | Ver [`limites`](#limites). |
| `revision` | objeto | no | (valores por defecto) | Paso de revisión del diff con el modelo. Ver [`revision`](#revision). |
| `archivos_protegidos` | lista de globs | no | `[]` | Ver [`archivos_protegidos`](#archivos_protegidos). |
| `requiere_agents_md` | booleano | no | `false` | Con `true`, `harness run` se detiene enseguida (código 1, antes de crear contenedores y de llamar al modelo) si el repo no tiene `AGENTS.md` o está vacío. Con `false`, solo avisa y sigue. Ver [`requiere_agents_md`](#requiere_agents_md). |

### `docker`

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `imagen` | texto | sí | | Imagen de Docker Hub donde corre el proyecto (`python:3.12-slim`, `maven:3-eclipse-temurin-21`...). Docker la descarga la primera vez. |
| `directorio_trabajo` | texto | no | `/app` | Ruta dentro del contenedor donde se monta el repo. Todos los comandos corren ahí. |
| `instalar` | texto o lista de texto | no | `[]` | Comandos de instalación. Corren **una vez**, en orden, al crear el contenedor. Si uno falla, la ejecución se detiene con error. |
| `reinstalar_si_cambia` | lista de globs | no | `[]` | Si el modelo modifica alguno de estos archivos (por ejemplo `requirements.txt`), el harness vuelve a ejecutar `instalar` antes del siguiente comando. |
| `cache` | booleano | no | `true` | Guarda lo que descargan pip, uv, npm, Maven y Gradle en el volumen de Docker `harness-cache-<proyecto>`, que sobrevive entre ejecuciones. Con `false`, cada ejecución descarga todo de nuevo. Ver [Caché de dependencias](#caché-de-dependencias). |

#### Caché de dependencias

- El volumen `harness-cache-<proyecto>` se monta en `/harness-cache` del contenedor del
  proyecto, **fuera del repo**: lo descargado nunca aparece en el repo, en Git ni en el PR.
  Por eso `directorio_trabajo` no puede ser `/harness-cache`, estar dentro de esa ruta ni
  contenerla.
- Cada herramienta se redirige con su variable: `PIP_CACHE_DIR`, `UV_CACHE_DIR` (y
  `UV_LINK_MODE=copy`), `npm_config_cache`, `GRADLE_USER_HOME` y, para Maven,
  `MAVEN_OPTS=-Dmaven.repo.local=/harness-cache/maven`. Si `variables_entorno` ya define una,
  se usa la del proyecto; si define `MAVEN_OPTS` sin `maven.repo.local`, la opción se agrega
  al final.
- Solo se guardan descargas: no se monta la caché del usuario (`~/.m2`, `~/.gradle`), no se
  guardan secretos, y antes de instalar se borran `init.d/` y `gradle.properties` de la caché
  de Gradle (Gradle los ejecutaría en cada build).
- El volumen se crea la primera vez y **nunca se borra solo**. Para una ejecución sin caché:
  `harness run --sin-cache` o `harness init --verificar --sin-cache`. Si otra ejecución del
  mismo proyecto está viva, la nueva corre sin caché y lo avisa.

### `comandos`

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `tests` | texto | sí | | Comando que corre los tests. Lo usa la herramienta `ejecutar_tests` y la fase de validación. En stacks compilados conviene que limpie antes de testear (ver la nota de abajo). |
| `lint` | texto o `null` | no | `null` | Comando del linter. Si es `null`, la herramienta `ejecutar_lint` no se le ofrece al modelo. |

- Se ejecutan con `sh -c "<comando>"` dentro de `directorio_trabajo`.
- **Código de salida 0 = pasó**; cualquier otro = falló, y la salida (recortada) vuelve al
  modelo.
- Los escribe una persona, así que no pasan por `comandos_permitidos`.
- **Limpiar antes de testear en stacks compilados.** La carpeta del proyecto está montada en el
  contenedor, así que sus artefactos de compilación (`target/` de Maven, `build/` de Gradle)
  sobreviven entre comandos y entre ejecuciones. Si el modelo borra o renombra un archivo, o
  se descartan los cambios de una ejecución anterior, sus `.class` viejos siguen ahí: Spring
  puede cargarlos y los tests de arquitectura los ven. Por eso conviene que `tests` limpie
  primero: `mvn -B -q clean test`, `gradle clean test`.

### `comandos_permitidos`

Programas que el modelo puede ejecutar con la herramienta `ejecutar_comando`, además de
`ejecutar_tests` y `ejecutar_lint`.

- Se compara la **primera palabra** del comando: con `[pytest, python]`, se permite
  `pytest -k crear`, pero no `rm -rf /app`.
- Se rechaza cualquier comando del modelo que contenga `;`, `&` (incluye `&&`), `|` (incluye
  `||`), `>`, `<`, `` ` ``, `$(` o un salto de línea (`\n`, `\r`), para que no pueda
  encadenar un programa no permitido detrás de uno permitido. Con `sh -c`, tanto `&` como el
  salto de línea separan comandos: `pytest & rm -rf /app`.
- Si la lista está vacía, el modelo no recibe la herramienta `ejecutar_comando`.
- No incluir instaladores de paquetes (`pip`, `npm`) salvo que el proyecto lo necesite: el
  modelo podría agregar dependencias por su cuenta.

### `servicios`

Contenedores extra que necesita el proyecto (base de datos, Redis...). Cada uno corre en la
red privada de la ejecución y se destruye al terminar.

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `nombre` | texto | sí | | **Dirección del servicio dentro de la red** (`db` → `postgresql://...@db:5432/...`). Formato `[a-z0-9-]+`, único entre servicios. El contenedor se llama `harness-<proyecto>-<ticket>-<id>-<nombre>`: así dos ejecuciones pueden tener cada una su `db`. |
| `imagen` | texto | sí | | Imagen de Docker Hub (`postgres:16`, `redis:7`...). |
| `variables` | mapa texto→texto | no | `{}` | Variables de entorno **del servicio** (`POSTGRES_PASSWORD`...). Admiten `${VARIABLE}`. |
| `listo_cuando` | texto o `null` | no | `null` | Comando que se ejecuta **dentro del servicio** cada segundo hasta que devuelve 0. Si es `null`, no se espera. |
| `timeout_listo_segundos` | entero | no | `60` | Si `listo_cuando` no responde en ese tiempo, la ejecución se detiene con error. |

Los servicios se levantan antes que el contenedor del proyecto, y el harness espera a que
todos estén listos antes de ejecutar `instalar`. Si un servicio no queda listo a tiempo, el
error incluye la última salida de `listo_cuando` y las últimas 20 líneas de `docker logs` del
servicio, donde suele estar la causa (una variable mal puesta, una contraseña vacía...).

**PostgreSQL: usar `pg_isready -h 127.0.0.1`.** Al arrancar por primera vez, la imagen de
postgres levanta un servidor temporal (solo por socket local) para crear la base y después lo
reinicia. `pg_isready` sin `-h` pregunta por el socket y puede responder "listo" justo antes de
ese reinicio. Con `-h 127.0.0.1` pregunta por TCP, que el servidor temporal no escucha, así que
solo responde cuando la base está lista de verdad.

Cómo se arma, por ejecución:

- Una red `harness-<proyecto>-<ticket>-<id>` (solo si hay servicios).
- Cada servicio corre en esa red con su `nombre` como alias, sin puertos expuestos al host.
- El contenedor del proyecto se conecta a la misma red y recibe `variables_entorno`.
- Al terminar (bien, con error o con Ctrl+C) se borran los contenedores y la red. Si una
  ejecución anterior se cortó sin limpiar, sus restos se borran al empezar la siguiente del
  mismo proyecto: se buscan por etiqueta (nunca por nombre) y solo se borran los de un proceso
  del harness que ya terminó. Una ejecución que sigue viva nunca se toca.
- Todo lo que crea el harness lleva la etiqueta `harness=1`, más `harness.proyecto`,
  `harness.ticket`, `harness.ejecucion` y el proceso que lo creó (`harness.pid` y
  `harness.host`): `docker ps -a --filter label=harness=1` lo lista.
- Las imágenes que falten se descargan antes de empezar (puede tardar la primera vez).

### `variables_entorno`

Variables que se pasan al **contenedor del proyecto** (no a los servicios).

- Valores de prueba van escritos directamente: `SECRET_KEY: clave-de-prueba`.
- Un valor secreto de verdad se escribe como `${NOMBRE}`. El harness lo reemplaza con la
  variable de entorno `NOMBRE` de la máquina del usuario (o del `.env` local en desarrollo).
- Si una variable `${NOMBRE}` no existe, la ejecución se detiene al arrancar con un mensaje
  que dice cuál falta.
- Una referencia puede ir dentro de un texto más largo:
  `DATABASE_URL: postgresql://app:${CLAVE_BD}@db:5432/app`.
- `${...}` también se puede usar en `servicios[].variables`.
- Los valores obtenidos de `${...}` **nunca se escriben en los logs** ni se le muestran al
  modelo; se reemplazan por `****`.
- Los valores se pasan a Docker por el entorno del proceso (`docker run -e NOMBRE`), no en la
  línea de comandos, donde cualquier proceso de la máquina podría leerlos.

### `git`

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `rama_base` | texto | no | `main` | Rama desde la que se crea la rama de trabajo (la **local**: después de un `git fetch`, el harness avisa si está atrasada o adelantada respecto del remoto). Es la base del pull request. El harness **nunca** hace push a esta rama, ni a `main` o `master`. |
| `prefijo_rama` | texto | no | `feature/` | La rama de trabajo se llama `<prefijo_rama><ticket>`, por ejemplo `feature/TAREAS-1`. Si ya existe (local o en el remoto), `harness run` se detiene antes de empezar. |
| `remoto` | texto | no | `origin` | Remoto de Git al que se hace push. Si su URL es de GitHub, ahí se abre el pull request. |

- La rama se crea al empezar, con el árbol de trabajo limpio; el commit, el push y el PR (en
  borrador) se hacen **solo si la validación pasó**. Si no, los cambios quedan sin commitear
  en la rama.
- El commit es de una línea: `<TICKET>: <título del ticket>` (la primera línea del ticket).
- `harness run --sin-push` hace solo el commit local; `--sin-pr`, commit y push sin PR.
  `--sin-cache` no usa la caché de dependencias (ver [`docker`](#docker)).
- Sin `harness.yaml` no hay validación, y por eso tampoco rama, commit ni push.

### `limites`

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `max_pasos` | entero | no | `30` | Máximo de pasos de **implementación** (cada respuesta del modelo es un paso). Cuenta recién desde que se aprueba el plan: la ronda de plan, sus correcciones, la revisión y la ronda de cambios fuera del plan tienen su propio presupuesto. Al llegar, la ejecución se detiene y se puede retomar con `harness resume`. |
| `max_pasos_plan` | entero | no | `10` | Pasos de cada ronda de plan (explorar con herramientas de lectura antes de llamar a `proponer_plan`), entero ≥ 1. Es un presupuesto propio: **no descuenta de `max_pasos`**, y `--max-pasos` no lo cambia. Cada corrección del plan con `--aprobar-plan` es otra ronda con estos mismos pasos. Al agotarse, el harness le pide el plan al modelo (con `tool_choice` donde el proveedor lo acepta) y le da una última oportunidad antes de cortar. |
| `max_intentos_validacion` | entero | no | `3` | Cuántas veces se vuelve de la validación (tests + lint) a la implementación antes de rendirse. |
| `timeout_comando_segundos` | entero | no | `120` | Tiempo máximo de cada comando dentro del contenedor. Si se pasa, se corta y el modelo recibe "tiempo agotado". |
| `max_caracteres_salida` | entero | no | `4000` | Largo máximo de la salida de una herramienta que se le devuelve al modelo. Si se pasa, el harness prioriza las **líneas que parecen errores** (`ERROR`, `FAIL`, `...Exception:`, `Caused by`, `expected`/`but was`, `E   assert` de pytest, `Traceback`...) con unas líneas de contexto, y con lo que queda conserva el **final** de la salida. Lo omitido se marca con `[... N líneas omitidas ...]`, y el texto fijo de ayuda de Maven ("Re-run Maven using the -X switch...") se descarta. El log de la ejecución guarda exactamente este mismo texto. |
| `max_caracteres_conversacion` | entero | no | `200000` | Tamaño máximo de la conversación (unos 50.000 tokens) antes de compactarla: los resultados de herramientas más viejos se reemplazan por una marca ("[Resultado omitido para ahorrar contexto: leer_archivo('src/x.py'), 120 líneas...]") y el modelo puede volver a pedirlos. Los últimos 6 pasos, el ticket, el plan y los textos del modelo nunca se tocan, y nunca se borra un mensaje. Se avisa en consola y queda en el log (`compactacion`). |
| `compactacion` | `marca` o `resumen` | no | `marca` | Cómo se compacta la conversación al pasar de `max_caracteres_conversacion`. `marca`: los resultados viejos se reemplazan por la marca de arriba, sin pedidos al modelo. `resumen`: un pedido aparte al mismo modelo (sin herramientas) resume esos resultados en hasta 2.000 caracteres; el resumen va en el lugar del primero ("[Resumen de resultados viejos, hecho para ahorrar contexto: …]") y los demás quedan como "[Incluido en el resumen de un resultado anterior: …]". Cuesta un pedido (sus tokens se suman a los del log y del PR); si el pedido falla, el resumen sale vacío o no ahorra, se usa la marca. Otro valor → error de validación. |

### `revision`

Paso de revisión: cuando la validación **pasa**, el modelo recibe el diff de lo que cambió en la
ejecución (sin `.harness/memory.md`, con los secretos ocultos y recortado a 30.000 caracteres)
y lo revisa contra las reglas de `AGENTS.md`, los criterios de aceptación del ticket y el
alcance (código que ningún criterio pide). Solo puede leer (`leer_archivo`,
`listar_directorio`, `buscar_en_codigo`) y termina con la herramienta `informar_revision`. Si
encuentra problemas, vuelve a implementar con todas las herramientas, se valida y se revisa
otra vez. Si se acaban las vueltas con problemas, **igual se hace el commit** (los tests
pasan): los problemas quedan en la sección "Revisión" del PR y en un aviso en consola.

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `activa` | booleano | no | `true` | Con `false` no hay revisión. Para una sola ejecución, `harness run --sin-revision` (o `harness resume --sin-revision`). |
| `max_vueltas` | entero | no | `2` | Máximo de revisiones por ejecución (≥ 1). Cada revisión con problemas, salvo la última, da otra vuelta de implementación y validación. |
| `max_pasos` | entero | no | `6` | Pasos de cada ronda de revisión (≥ 1). **No descuentan de `limites.max_pasos`**: la revisión no le quita pasos a la implementación. El último se reserva para pedir el informe con `tool_choice`. |

- Gasta pedidos: de 1 a 3 por vuelta, según cuánto lea el modelo.
- Si el modelo no llama a `informar_revision`, se avisa, se sigue sin revisión y el PR lo
  dice.
- Las validaciones que siguen a una corrección pedida por la revisión no cuentan para
  `max_intentos_validacion`.

### `archivos_protegidos`

Rutas (globs, relativas a la raíz del repo) que el modelo **no puede crear, modificar ni
borrar**. Las herramientas `escribir_archivo` y `editar_archivo` las rechazan.

Además de las que se pongan aquí, **siempre** están protegidas, aunque no se escriban:

- `harness.yaml`: para que el modelo no pueda aflojar sus propios límites.
- `.git/**`
- `.harness/sessions/**` y `.harness/logs/**`
- `.harness/memory.md`: solo cambia nota por nota, con la herramienta `guardar_memoria`
  (nunca reescrito entero con `escribir_archivo`).

Hay además archivos que el modelo **no puede ni leer**, igual que `.git/`:

- `.env` y `.env.*` (por ejemplo `.env.local`, `.env.production`), en cualquier carpeta del
  repo y sin importar mayúsculas: suelen tener secretos reales (API keys, contraseñas), y
  lo que lee el modelo se envía a la API del proveedor. `leer_archivo` y `escribir_archivo`
  los rechazan con un error que dice que el archivo puede contener secretos.
- Se permite `.env.example`: es la plantilla sin valores reales que se sube a Git.
- La comprobación se hace sobre la ruta ya resuelta, así que un symlink con otro nombre que
  apunta a un `.env` también se rechaza.
- `listar_directorio` sí muestra que el archivo existe (el nombre no es un secreto).
- **Dentro del contenedor los `.env` se ven vacíos.** Las herramientas no son el único
  camino: el repo se monta completo, y `cat .env` (o un test que lo imprima) le mostraría su
  contenido al modelo. Por eso, al crear el contenedor, el harness monta un archivo vacío de
  solo lectura encima de cada `.env` y `.env.*` del repo (también en subcarpetas, saltando
  `.git`, `.venv`, `node_modules` y las demás carpetas ignoradas). `.env.example` se ve normal.
  El `.env` del disco no se modifica.
- Por lo mismo, **los tests no pueden depender de un `.env`**: si un proyecto necesita
  variables para correr sus tests, se declaran en `variables_entorno` (con `${NOMBRE}` si
  son secretas).

Cómo se hace cumplir:

- `escribir_archivo` (y `editar_archivo`) rechazan escribir en cualquiera de esas rutas.
- Dentro del contenedor, `.git`, `harness.yaml` y `.harness/` se montan **de solo lectura**.
  Es importante para `.git`: un hook escrito desde el contenedor (`.git/hooks/pre-commit`) se
  ejecutaría en la máquina del usuario cuando el harness haga commit.
- Los globs de `archivos_protegidos` no se pueden montar de solo lectura (son patrones), y un
  comando permitido como `python` o un test escrito por el modelo podría modificarlos. Por eso,
  después de instalar las dependencias, el harness guarda una copia (y su hash) de cada archivo
  que coincide. Después de **cada comando** del contenedor (`ejecutar_comando`,
  `ejecutar_tests`, `ejecutar_lint` y la validación final) compara: si uno cambió o
  desapareció, lo restaura; si apareció uno nuevo en una ruta protegida, lo borra. En los dos
  casos agrega un aviso al resultado del comando para que el modelo lo sepa.

### `requiere_agents_md`

Sirve para las ejecuciones automáticas, sin nadie mirando la consola: ahí el aviso de "falta
AGENTS.md" pasa desapercibido y el modelo trabajaría sin las convenciones del proyecto.

- Solo afecta a `harness run`. `harness init` genera el archivo que falte (uno o los dos) sin
  importar el valor de esta clave; un `AGENTS.md` vacío cuenta como faltante.
- El `harness.yaml` que genera `harness init` siempre la incluye, explícita y en `false`:
  `requiere_agents_md: false  # true = harness run se detiene si falta AGENTS.md`.

---

## Reglas de validación

- **Claves desconocidas = error.** Así se detectan errores de tipeo (`comando:` en vez de
  `comandos:`) en lugar de ignorarlos en silencio.
- Falta una clave obligatoria o un tipo no coincide → error con la ruta exacta
  (`docker.imagen: falta este campo`).
- `version` distinta de `1` → error: "versión de harness.yaml no soportada".
- Nombres de `proyecto` y `servicios[].nombre` fuera de `[a-z0-9-]+`, o servicios repetidos →
  error.
- `docker.directorio_trabajo` igual a `/harness-cache`, dentro de esa ruta o que la contenga
  → error: "no puede coincidir con la caché de dependencias (/harness-cache)".
- `revision.max_vueltas` o `revision.max_pasos` menores que 1 → error.
- Si no existe `harness.yaml`, el harness avisa y sugiere `harness init`.

## Implementación

- Leer con `yaml.safe_load` de `pyyaml` (**nunca** `yaml.load`, que puede ejecutar código).
- Validar con modelos de `pydantic` con `extra="forbid"` (eso da el error de claves
  desconocidas gratis) y valores por defecto declarados en los modelos.
- Dependencias: `uv add pyyaml pydantic`.
- Todo el resto del harness recibe el objeto ya validado, nunca el diccionario crudo.

---

## Reglas de arquitectura

Las reglas de `AGENTS.md` ("los controladores no usan repositorios", "ventas no importa de
productos") solo las respeta el modelo si las sigue: los tests no las ven, y en TIENDA-3 el
modelo usó `ProductoRepository` desde ventas con todos los tests en verde. La revisión
([`revision`](#revision)) ayuda, pero lo que de verdad las hace cumplir es una herramienta que
falle la validación. `harness init` recomienda una según el stack, si el proyecto no usa ya
ninguna (no modifica el build).

**Java (Maven o Gradle): ArchUnit.** Es un test más, así que corre con `comandos.tests` sin
cambiar nada del `harness.yaml`.

```xml
<!-- pom.xml (en Gradle: testImplementation "com.tngtech.archunit:archunit-junit5:1.3.0") -->
<dependency>
  <groupId>com.tngtech.archunit</groupId>
  <artifactId>archunit-junit5</artifactId>
  <version>1.3.0</version>
  <scope>test</scope>
</dependency>
```

```java
@AnalyzeClasses(packages = "com.ejemplo.tienda")
class ArquitecturaTest {
    @ArchTest
    static final ArchRule ventasNoUsaProductos = noClasses()
        .that().resideInAPackage("..ventas..")
        .should().dependOnClassesThat().resideInAPackage("..productos.repositorio..");
}
```

**Python: import-linter.** Los contratos van en `.importlinter` (o en `pyproject.toml`), y se
corre con `lint-imports`:

```ini
[importlinter]
root_package = tareas

[importlinter:contract:capas]
name = Capas
type = layers
layers =
    tareas.main
    tareas.repositorio
    tareas.modelos
```

```yaml
docker:
  instalar: pip install -e ".[dev]" import-linter
comandos:
  tests: pytest -q
  lint: lint-imports
```

**Node: dependency-cruiser** (o `eslint-plugin-boundaries`, si el proyecto ya usa ESLint):

```yaml
comandos:
  tests: npm test
  lint: npx depcruise src --config .dependency-cruiser.js
```

En los tres casos, conviene proteger el archivo de las reglas para que el modelo no las
afloje para que pase la validación:

```yaml
archivos_protegidos:
  - src/test/**/ArquitecturaTest.java   # o .importlinter, .dependency-cruiser.js
```

## Lo que NO va en `harness.yaml`

| Qué | Dónde va | Por qué |
|---|---|---|
| Proveedor y modelo de IA | `~/.harness/config.toml` | Cada usuario elige su modelo y paga (o no) su API; el proyecto no lo decide. |
| API keys, tokens de GitHub y Jira | `~/.harness/config.toml` o variables de entorno | Son secretos personales. |
| Arquitectura, convenciones, ejemplos de código | `AGENTS.md` | Eso lo lee el modelo, no el harness. |
| El `Dockerfile` / `docker-compose.yml` del proyecto | Se quedan como están | El harness no los usa: son para correr la aplicación, no para los tests. |
