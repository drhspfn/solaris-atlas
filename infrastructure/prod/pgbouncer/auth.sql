-- Run over the existing container-local PostgreSQL socket. Never log this result.
-- Use the exact server verifier: generating a new salt from the password breaks pass-through.
SELECT '"' || replace(rolname, '"', '""') || '" "' || rolpassword || '"'
FROM pg_authid
WHERE rolname = current_user
  AND rolcanlogin
  AND (rolvaliduntil IS NULL OR rolvaliduntil > now())
  AND rolpassword LIKE 'SCRAM-SHA-256$%';
