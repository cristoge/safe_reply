# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Estado del proyecto

El repo está **cambiando de dominio**: empezó como un RAG de apuntes (proyectos, documentos, chunks) y se está convirtiendo en un **sistema de tickets con IA para un banco digital ficticio**. El dominio de apuntes ya se ha borrado (S2, migración `66a27c1f8fdc`; sigue en el historial de Git). El esquema de la v1 son 6 tablas: `users` (+ `role`, `phone`), `refresh_tokens`, `tickets`, `ticket_messages`, `ticket_corrections` y `ticket_events` (migración `d29766035959`). El primer mensaje del cliente vive en `tickets.text` (inmutable); `ticket_messages` es la conversación posterior. Hecho hasta el S6: registro de clientes con teléfono (E.164), agentes solo por script, `POST/GET /tickets` (rate limit, `Idempotency-Key`, historial tipo chat), el **procesado sin LLM** dentro del mismo `POST /tickets` (clasificar → reglas → urgencia → router → plantilla de llamada o respuesta predefinida) la **cola humana** (reclamar, llamar, resolver, corregir; el cliente escala o contesta) y las **57 respuestas predefinidas** (`policies/`). Siguiente: el cierre de la v1 (S7).

El diseño y el plan viven fuera del repo, en el vault de Obsidian del usuario:
- `~/vault/Prog/PROYECTOS/revision_rag/Sistema de tickets con IA - diseño.md` — flujo, reglas, esquema de BD (apartado 8), problemas previstos (14), resultados de `ml/` (3.4 y 3.5).
- `~/vault/Prog/PROYECTOS/revision_rag/Sistema de tickets con IA - sprints.md` — el plan vigente, con casillas: **v1 sin LLM** (predefinidas, procesado en la misma petición) y **v2** (RAG con pgvector + colas con ARQ).

Leerlos antes de cambios de diseño. Al cerrar un sprint, el usuario actualiza ahí lo que cambió y por qué.

## Comandos

Python 3.14 y uv (versiones fijadas en `mise.toml`). Todo desde la raíz del repo.

```bash
mise run docker:up        # Postgres (pgvector) + Redis, espera a que estén healthy
mise run fastapi          # docker:up + uvicorn --reload → http://127.0.0.1:8000/docs (necesita models/: mise run ml:train)
mise run docker:down
mise run agent:create -- agente@banco.com --name Ana   # los agentes solo se crean así (pide la contraseña)

uv run pytest                                          # toda la batería
uv run pytest tests/unit/test_intent_rules.py          # un archivo
uv run pytest tests/api/test_auth.py::test_login_returns_the_tokens   # un test

uvx ruff check . && uvx ruff format .
uvx basedpyright                                       # config en pyproject (modo standard)

uv run alembic revision --autogenerate -m "..."        # revisar SIEMPRE el archivo generado
uv run alembic upgrade head / downgrade -1

mise run ml:data                       # descarga Banking77 en ml/data/banking77/ (checksums en ml/banking77.sha256)
mise run ml:train                      # entrena y guarda models/intent_classifier.joblib + .json (fuera de Git)
uv run python ml/compare_models.py     # evaluación del router sobre el test de Banking77
uv run python ml/robustness.py         # fuera de ámbito, intenciones escondidas, dos problemas, largos
uv run python ml/try_router.py "my card was stolen"   # un ticket a mano: cada paso y su motivo
```

- **Todos los tests necesitan Docker en marcha** (incluidos los de `tests/unit/`): el `conftest.py` raíz tiene un fixture de sesión `autouse` que recrea la BD `rag_test` y aplica todas las migraciones.
- La app no arranca sin Postgres y Redis (el `lifespan` hace `SELECT 1` y `ping`: fail fast), ni sin el modelo o con un YAML inválido (`load_triage` en `main.py`: reglas + modelo + respuestas predefinidas, comprueba que modelo y YAML tengan las mismas intenciones, la versión de scikit-learn y que haya exactamente una respuesta por intención de IA). Necesita `.env` (ver `.env.example`; `JWT_SECRET_KEY` de ≥ 32 caracteres).
- `ml/data/banking77/` (train/test CSV de PolyAI) y `models/` no están en Git: se regeneran con `mise run ml:data` y `mise run ml:train` (5 s; el modelo pesa 15 MB y cada reentrenamiento añadiría otra copia al historial). El `.json` de metadatos guarda la versión (→ `tickets.classifier_version`) y la de scikit-learn con la que se guardó.

## Arquitectura del backend (`src/revision_rag/`)

Capas, cada una solo habla con la de abajo: `api/routes` → `api/deps.py` → `services` → `repositories` → `models`.

- **`api/deps.py`** construye repositorios y servicios por petición con `Depends`. FastAPI cachea cada dependencia dentro de una petición, así que repositorio y servicio **comparten la misma `AsyncSession`** (una transacción). Los alias `Annotated` (`CurrentUser`, `DbSession`, `Pagination`...) son la forma de inyectar. El rol se comprueba ahí con `AgentUser` / `CustomerUser` (`require_agent` / `require_customer` → **403**), no en los servicios.
- **Los servicios hacen `commit()`; los repositorios nunca** (solo `flush()`).
- **Errores:** los servicios lanzan subclases de `AppError` (`core/exceptions.py`) con `status_code` y `detail`; un único handler en `main.py` las traduce a HTTP. Los servicios no saben nada de HTTP.
- **Patrón del dueño (IDOR):** las consultas de los repositorios filtran siempre por el dueño (`get_for_user`), y lo ajeno da **404, no 403** (no confirmar que existe). La comprobación se centraliza en una dependencia (`get_owned_<recurso>` → alias `Owned<Recurso>`), como hacía `get_owned_project` antes del S2. Los tickets siguen el mismo patrón por `customer_id`.
- **Auth:** JWT de acceso de corta duración + refresh tokens opacos guardados como SHA-256 y **rotados** en cada uso (`FOR UPDATE`). Login con hash ficticio cuando el email no existe (contra timing attacks). argon2 en otro hilo (`asyncio.to_thread`).
- **Redis** (`db/cache.py`): un cliente compartido en `app.state`. Solo para lo que no necesita escribir en Postgres: el rate limiting (`services/rate_limiter.py`, ventana fija con `INCR` + `EXPIRE NX`) y, en la v2, la cola (ARQ). Nunca caché de respuestas. La `Idempotency-Key` de `POST /tickets` vive en Postgres (`tickets.idempotency_key` + índice único parcial), en la misma transacción que el ticket: Postgres es la fuente de verdad.

### Procesado de tickets (S4)
- `TicketService.create` guarda el ticket + evento `created` (commit) y **después** llama a `TaskQueue.enqueue_ticket`. v1: `InlineTaskQueue` (`tasks/queue.py`) ejecuta `TicketProcessor.process_ticket` en la misma petición; v2: `ArqTaskQueue` sin tocar el procesado.
- `services/ticket_processor.py`: transición condicional `received → processing` (`UPDATE … WHERE status='received' RETURNING`; si ya no está en `received`, no hace nada) y commit; luego `triage/` decide y se guarda todo (campos del ticket, mensaje `system`, evento) en otro commit. **Si algo falla → rollback y a persona** (`route_reason="processing error"`, prioridad high, plantilla): ningún ticket se queda atascado.
- `triage/` son funciones puras: `rules.py` (YAML validado con Pydantic: un YAML mal formado no carga), `router.py`, `urgency.py` (prioridad final, `priority_reason`, `due_at` solo para la cola humana, desde `created_at`), `answers.py` (`CALL_TEMPLATE` y `CannedAnswers` de `policies/canned/<intent>.md`, sin texto de reserva: si faltara una, `KeyError` → persona). Con `rag_enabled: false` (YAML), lo que el router manda al RAG responde con su predefinida.
- Reglas, modelo y respuestas viven en `app.state` (cargados en el `lifespan`) y se inyectan con `get_rules` / `get_classifier` / `get_canned_answers`.

### Respuestas predefinidas (S6)
- `policies/bank_facts.md` es la única fuente de los datos del banco ficticio (plazos, comisiones, límites, rutas de la app como "Help › Contact us"); en la v2, la base del manual del RAG. Las reglas públicas reales (SEPA, PSD2, pagos instantáneos) se siguen; lo que controla el banco va con "usually".
- `policies/canned/<intent>.md`: una por intención de IA (57: 29 `canned` + 28 `rag`; las `rag` son genéricas a propósito, "the most common reasons are…"). Todas acaban con el mismo pie: aviso de que es automática + "I still need help".
- `tests/unit/test_canned_answers.py` (contra los archivos reales): una por intención de IA y ninguna más, sin marcadores, con el pie, y **cada número y cada ruta de la app aparecen en `bank_facts.md`**. Una frase sin cifras ni rutas que no salga de los datos no la detecta: eso es revisión a mano. Cambiar un dato = cambiar `bank_facts.md` y todas las respuestas que lo citan.

### Cola humana (S5)
- Agente (`api/routes/agent.py` → `services/human_queue.py`): `GET /agent/queue` es solo para mirar; se atiende con `POST /agent/queue/claim` ("dame el siguiente" por `due_at`, `FOR UPDATE SKIP LOCKED`; 204 si está vacía). **El agente no elige ticket** (si eligiera, los difíciles esperarían).
- Reclamado y sin `contacted` durante `human_queue.claim_timeout_hours` (YAML) → reclamable otra vez **en la propia consulta** (`assigned_at < now - X`), sin barrido; queda un evento `unassigned` + `assigned`.
- Las acciones (`contacted`, `call-failed`, `resolve`) bloquean la fila (`get_for_update`) y solo las hace el agente asignado (**403** si no; los agentes ven todos los tickets, no hay nada que esconder con un 404). Repetirlas no hace nada (idempotentes por el estado), salvo `call-failed`.
- `max_failed_calls` (YAML) llamadas fallidas → mensaje `unreachable_message` (+ "freeze your card" en `security_and_fraud`), el ticket vuelve a la cola sin asignar y evento `unassigned` con `reason="customer unreachable"`: desde ese evento la cuenta empieza de cero (`count_failed_calls`, por id uuid7). Un reclamado y abandonado **no** la reinicia.
- Resolver sin haber contactado: sí, con motivo obligatorio (422 si falta).
- Cliente: `POST /tickets/{id}/escalate` ("I still need help") y `POST /tickets/{id}/messages`. Desde `answered` → escalar (prioridad +1 como mucho high, `escalated_at`, `due_at` desde que escala, plantilla de llamada; `route`/`route_reason` no cambian). En `in_human_queue`/`contacted`, contestar se adjunta (evento `customer_replied`). `resolved` → 409 "abre uno nuevo"; `received`/`processing` → 409. Escalar algo que ya está con una persona no hace nada.
- Correcciones: `POST /agent/tickets/{id}/corrections` (cualquier agente, cualquier estado); los `predicted_*` se copian del ticket; una `corrected_intent` que no está en el YAML → 422.

### Convenciones de modelos y migraciones
- PKs `uuid.uuid7` generadas en Python (ordenadas por tiempo: sirven para desempatar `ORDER BY created_at, id`; `now()` de Postgres es la hora de inicio de la transacción, así que varias filas de una misma transacción comparten `created_at`).
- Enums con `str_enum()` (`models/enums.py`): varchar + `CHECK`, nunca ENUM nativo de Postgres. Los valores de reglas de negocio (`intent`, `ticket_type`) van como varchar **sin** `CHECK`: la lista válida es el YAML.
- `NAMING_CONVENTION` en `db/base.py` da nombres fijos a índices y restricciones (Alembic depende de ello).
- Relaciones con `lazy="raise"` (en async no hay carga perezosa: cargar de forma explícita) y `passive_deletes=True` con `ondelete="CASCADE"`.
- `TimestampMixin` usa `eager_defaults` para leer `updated_at` sin recargar (evita `MissingGreenlet`); sesiones con `expire_on_commit=False`.
- **No editar migraciones ya aplicadas**: siempre una nueva. `tests/integration/test_migrations.py` ejecuta `alembic check` y falla si modelos y migraciones no coinciden. `migrations/env.py` toma la URL de `Settings`, no de `alembic.ini`.

### Tests
- `tests/unit` (funciones puras), `tests/integration` (repositorios/servicios contra Postgres real), `tests/api` (HTTP con `httpx.ASGITransport`, sin servidor).
- `db_session`: cada test dentro de una transacción con `join_transaction_mode="create_savepoint"` que se deshace al final → los `commit()` del código probado no persisten.
- Redis de tests = base de datos 1 (`FLUSHDB` por test); la de desarrollo es la 0.
- `ASGITransport` no ejecuta el `lifespan`: el `client` de `tests/conftest.py` sustituye `get_rules` (el YAML real), `get_classifier` (`FakeClassifier` de `tests/fakes.py`: duda por defecto → persona; `classifier.answer(text, intent)` fija una predicción; `classifier.error` simula un fallo) y `get_canned_answers` (las respuestas reales de `policies/canned/`). `pythonpath = ["tests"]` (pytest) y `extraPaths` (basedpyright) permiten `from fakes import ...`. Ningún test necesita `models/`.
- `tests/api/conftest.py`: `login_headers()` / `auth_headers` / `other_auth_headers` (clientes con teléfono; el segundo, para probar el patrón del dueño) y `agent_headers` / `other_agent_headers` (agentes creados directamente en la BD, porque por la API no se puede).
- `tests/integration/test_claim_concurrency.py` usa conexiones reales (no `db_session`): sus datos se confirman de verdad y los borra al terminar.

## `ml/` y las reglas de negocio

- `rules/intents.yaml` es la **única fuente de las reglas de negocio**: las 77 intenciones de Banking77 (tipo, prioridad, `route` ai/human, `answer_mode` canned/rag, `min_confidence`, `reason`), los umbrales del router, los plazos por prioridad y `sensitive_patterns` (red de seguridad: palabras de fraude → persona). Lo carga y valida con Pydantic `triage/rules.py` (`load_rules`): un YAML mal formado no carga. `tests/unit/test_intent_rules.py` vigila las decisiones de negocio (fraude siempre a persona...); cualquier cambio del YAML debe mantenerlo en verde.
- **Un solo router:** `src/revision_rag/triage/router.py` (`summarize` + `decide_route`, reglas en orden 0 palabra sensible → 1 siempre persona → 2 confianza → 3 P(persona) → 4 predefinida → 5 RAG, cada una con su `route_reason`). Lo usa el backend y también `ml/`: `ml/common.py` (carga de datos, modelo definitivo TF-IDF + regresión logística) no tiene reglas propias; su `decide_routes` solo pasa cada ticket por `decide_route` con los umbrales que se quieran comparar. Así lo que se mide es lo que se ejecuta.
- Los scripts de `ml/` importan con `from common import ...`: ejecutarlos como `uv run python ml/<script>.py` desde la raíz (no como módulos).
- Las decisiones del router se toman **midiendo** con `compare_models.py`/`robustness.py`; los resultados y lo descartado están en el apartado 3.5 del diseño.

## Convenciones

- Comentarios, docstrings, mensajes de error de la API y commits en **español**. Commits con Conventional Commits en español (`feat: añadir ...`, `refactor: ...`, `chore: ...`).
- Lo que ve o genera el dominio va en **inglés**: textos de tickets, respuestas predefinidas, manual de políticas, prompts y nombres en la BD. Los clientes escriben siempre en inglés (no hay detección de idioma).
- Los comentarios del código explican el **porqué** (decisiones, trampas), no el qué; mantener ese estilo.
