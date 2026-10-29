# Operations and recovery (initial)

## Health checks

- `GET /health/live/` confirms the web process can serve a request without exposing dependencies.
- `GET /health/ready/` checks database connectivity and returns only a generic unavailable status on failure.

## Local startup

Use the Docker Compose PostgreSQL service for integration testing. Run `python manage.py migrate` as an explicit step. The Compose web process runs Django's development server and is not suitable for production.

## Deployment outline

1. Build and scan the immutable Docker image.
2. Back up PostgreSQL and confirm a recent restore test before a risky schema change.
3. Apply reviewed migrations once as a release step; do not run migrations independently in every web replica.
4. Deploy the Gunicorn image behind a TLS-terminating reverse proxy. Configure forwarded protocol only when the proxy strips client-supplied equivalents and sets its own trusted header.
5. Configure persistent PostgreSQL, secret manager, secure cookie/HSTS settings, allowed hosts/origins, SMTP, log collection, and database backups.
6. Check liveness/readiness and execute a read-only storefront smoke check. Roll back to the previous image if health checks or smoke checks fail.

## Release and rollback procedure

Use this procedure only after the launch gates are approved and a staging environment has passed the same release candidate.

1. Record the immutable image digest and the currently deployed image digest.
2. Confirm the current PostgreSQL backup completed and that its restore procedure has been rehearsed against a non-production database.
3. Run the release migration command once, under the release operator account. Do not run it concurrently from web replicas.
4. Deploy the immutable image with payment, supplier, points, and giveaway flags in their approved states. Unapproved flows remain disabled.
5. Verify `/health/live/` and `/health/ready/` return HTTP 200, then perform a read-only storefront smoke check and inspect application logs by request ID.
6. Watch error rate and request latency for the agreed observation window. Stop the rollout on a data-integrity issue, a newly discovered security issue, error rate more than twice baseline, or p95 latency more than 50% above baseline.

To roll back, disable an affected feature flag when one exists; otherwise redeploy the recorded previous image. Recheck both health endpoints and the storefront smoke path. Preserve the database and audit evidence. Do not reverse a migration or modify production data without an approved, migration-specific recovery plan and a restored-backup rehearsal.

## Local Compose preflight

Before local PostgreSQL integration testing, copy `.env.example` to `.env`, replace the local-only values as needed, and start Docker Desktop. `docker compose config --quiet` requires the Compose `env_file` to exist; a missing `.env` prevents a full local startup. The Compose file is not a production deployment configuration.

## Current operational gaps

There is no queue worker, scheduled synchronization job, metrics backend, alert destination, automated backup, automated deployment, or restore rehearsal yet. Payment reconciliation, refund, supplier sync, queue, and secret-rotation procedures will be completed before those systems are enabled. Docker Compose configuration rendered on 2026-10-30, but no local service startup was possible because Docker Desktop was unavailable in the verification environment.
