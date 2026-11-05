# Selective application releases

Main pushes build all images, then release **API and web only**, pinned to the
full commit SHA and resolved immutable image IDs. Model builds continue without
blocking application delivery. `Deploy app safely` provides the same manual path.

The helper uses installed production compose/env files under the existing
deployment lock; it stages only its script, never overwrites live configuration,
and never restarts PgBouncer, infrastructure, or workers. A new API-image check
runs from `/app/packages/server`, in a read-only transaction, and requires the
database Alembic heads to match the image. Pending migrations fail the release
before API/web replacement. Schema/configuration changes require separate
explicit maintenance; they are not silently skipped or applied online.

Receipts and CLI logs live in `~/solaris-atlas/.deployments/app-<sha>/`.
Command intent is durable before execution; children retain the deployment lock.
An interrupted replacement is verified rather than blindly replayed. API health
must pass before web changes. Worker/infrastructure IDs and PIDs are checked
before and after. Token login uses temporary `DOCKER_CONFIG`; host auth is retained.

## Deferred worker updates

Workers and models **are not updated by this application job**. Their images still
build; the app receipt and GitHub Summary list the deferred worker containers and
the release SHA. Operators must schedule the worker rollout explicitly.

For busy workers, capture exact container IDs/images/resources and journal them;
signal only those IDs with `docker stop --timeout=-1`, allow their current jobs to
finish, require clean exit, then create/start only the reviewed replacements.
Roll individual services so one long import does not hold every pool offline.
Do not use a 120-second timeout or force recreation of an active worker.

The existing **Deploy selected services** workflow, including explicit **all**,
is unchanged. It remains the full maintenance/configuration/migration path and
can stop clients/reconcile workers: do not use `all` as an import-safe app release.
Use it only in an explicitly coordinated maintenance window after safe draining.
No approval gate or permanent workflow disabling is introduced here.
