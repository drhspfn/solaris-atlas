# Backend ORM console

The console is mounted by FastAPI at `/console` (direct API: `http://localhost:8000/console/`).
Caddy routes `/console` and its assets to the API on the main site too.
It uses **Starlette-admin's SQLAlchemy adapter**: the library queries the mapped
models directly and generates tables, filters, forms, details and confirmation
dialogs. The budget overview uses reusable server-rendered components; it has no
separate frontend application or REST data provider. The Python registration lives in
`packages/server/src/wuwa_story/admin/composition.py`. The shared dark theme is in
`packages/server/src/wuwa_story/admin/statics/solaris-admin.css`; it adapts
Tabler and the table and select widgets to the Solaris palette.

## Accounts and sessions

Existing active administrator accounts work immediately. The console uses the
same Argon2 password verification and opaque `auth.session` cookie as the API.
Roles, revocations and expiry are checked on every request. No extra admin user
table, session store, password default or schema migration is introduced.

To provision the first administrator after the normal database migrations:

```sh
cd packages/server
uv sync --frozen
uv run alembic upgrade head
uv run wuwa-story-admin create-admin operator@example.com operator
```

The password is entered twice without echo. For controlled deployment automation,
`--password-env INITIAL_ADMIN_PASSWORD` reads an explicitly supplied environment
secret without placing its value in command arguments. Existing accounts can
instead use the existing `promote <email>` command. CLI creation does not create
a browser session.

User forms hash new passwords, normalize identities, preserve a blank password
on edits, prevent removing the last active administrator, and revoke sessions
after password/role/status/email changes. Session rows expose metadata and a
Revoke action, never token hashes. Sign-out revokes the same database session.

## Included ORM pages

- Agent: open alerts, research runs, open questions, tool/admin audit, memory, work ledger,
  jobs, provider call accounting, daily usage and research notes.
- Accounts: users and sessions.
- Archive: quests, characters, releases, nodes, documents, events, notices and locales.

Open alerts expose their source citations and a link to the associated research run.
The resolve action updates only open alerts and records the administrator in the audit.
The former `/admin/alerts` page redirects to `/console/alerts/list`.

The daily budget usage page shows the configured daily cost and token limits,
budget timezone, current budget day, and the read-only spending and reservation
ledger. The former `/admin/usage` page redirects to `/console/usage/list`.

Users, memory and manual analysis tasks support creation/editing. Imported facts,
execution history and audit records are read-only. Question answers remain on the
existing cited-publication path; the generic form cannot bypass source validation.
Memory uses the existing revision-checked service and embedding adapter, so old
vectors are invalidated on edits. Saving long memory may use the configured
embedding model and shared budget. Stale edits are rejected.

Native row actions revoke sessions, resume supported paused research runs, and
queue quest analysis through the existing services. They use the library's
confirmation modal and retain provider/budget/reconciliation rules. Merely
opening the panel does not call an AI provider or enable autonomous execution.
Changes are recorded in the existing tool audit table with role `admin`, actor
ID and record ID; passwords and session tokens are not recorded.

## Adding a model or a custom page/action

Register another SQLAlchemy model; no CRUD endpoints or HTML are needed:

```python
from starlette_admin.contrib.sqla import ModelView

class CatalogView(ModelView):
    fields = ["id", "name", "description"]
    searchable_fields = ["name"]

admin.add_view(CatalogView(Catalog, label="Catalog"))
```

Use the project's `ReadOnly`/`Editable` bases for the bounded paging, safe errors
and deletion policy. `ScalarForeignKeys` displays scalar foreign-key columns in
this codebase, which deliberately has few ORM relationship attributes. Define
native `@row_action` handlers for domain actions. `OpenQuestions` demonstrates a
custom filtered page through `get_list_query`/`get_count_query`, rendered by the
same library table components, excluding superseded document heads.

## Security and deployment

The library's generated forms and AJAX are protected by exact Origin/Referer
verification. Missing or cross-origin provenance is rejected for writes and for
the library's GET action/logout routes. Do not remove this middleware. Authenticated
responses use `private, no-store`; frame embedding is denied. Public/regular users
cannot open model APIs, exports are disabled, and rows per request are capped at 100.

The production API has no published container port and trusts Caddy's forwarded
headers through `FORWARDED_ALLOW_IPS=*` on that private service. This preserves
HTTPS form origins and generated links. If exposing the API directly, restrict
forwarded-header trust to the actual proxy addresses instead.

Starlette-admin 0.17.1 relies on SQLAlchemy internals removed in 2.1. Server and
worker locks therefore use the supported SQLAlchemy 2.0 series (currently 2.0.56).
Two cutscene queries use the equivalent 2.0 `distinct(columns)` PostgreSQL
DISTINCT ON syntax. Lift the <2.1 constraint after upstream compatibility lands.

## Verification

The PostgreSQL integration tests create a separate migrated database and cover
native login, live role checks, CSRF provenance, generated user/memory/work forms,
password hashing, last-admin protection, session revocation, stale revisions,
immutable history, audit receipts, filtered questions and CLI provisioning.
The full server run passed 1180 tests with 99 optional tests skipped; the existing
three MCP/health tests pass in a separate process because their session manager
has a known repeat-lifespan conflict with the auth integration suite. No paid
model execution is part of these checks.
