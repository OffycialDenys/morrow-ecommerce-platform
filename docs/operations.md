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

## Current operational gaps

There is no queue worker, scheduled synchronization job, metrics backend, alert destination, automated backup, automated deployment, or restore rehearsal yet. Payment reconciliation, refund, supplier sync, queue, and secret-rotation procedures will be completed before those systems are enabled.
