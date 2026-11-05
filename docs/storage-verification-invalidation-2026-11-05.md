# Verification-only storage updates

Media registration records `file_location.verified_at` even when an existing
object, URL, ETag and availability are unchanged. The storage statement trigger
previously invalidated public projections for these timestamp-only updates.
During the recovery run PostgreSQL reported lock timeouts on projection scopes;
character scope generations advanced while imports reverified shared objects.

Migration `0029_storage_verification_noop` preserves verification timestamps and
all existing advisory/isolation fences. Its AFTER trigger returns early only
when every updated location differs solely in `verified_at`, or nothing changed.
Real field changes, inserts, deletes and mixed batches retain the original path.
No tables, indexes or source records are rewritten.

Disposable PostgreSQL tests verify zero dirty projection rows after timestamp-only
updates, dirty rows after real changes, and restored invalidation after downgrade.
This removes unnecessary projection work; it does not eliminate row contention
between concurrent timestamp writers or promise a particular throughput gain.

## Online rollout

Use the CI-built API image containing the migration, with the direct migration
database connection. Set `WUWA_ONLINE_STORAGE_VERIFICATION_ONLY=1` and explicitly
target `0029_storage_verification_noop`. The guard accepts only an existing single
head at `0028_agent_reader_role`; it rejects `head`, other revisions and replays.
Do not enable the separate online search-index migration flag.

The migration replaces one compatible function with a five-second lock timeout.
It can run without restarting API, PgBouncer or import workers. Record the prior
revision and function definition, verify the new revision, then sample projection
generations, retry errors and public HTTP responses during ongoing imports.
Rollback to `0028_agent_reader_role` restores the previous function and does not
remove source data. Coordinate rollback explicitly; retain the ordering fences.
