# Environment variable reference

Use `.env.example` as a local development template only. Store production secrets in a secret manager or deployment environment, never in source control.

| Variable | Purpose | Production requirement |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django signing and CSRF/session cryptographic key. | Required, unique, random, at least 50 characters. |
| `DJANGO_DEBUG` | Enables local debug/static behavior. | Must be `false`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames accepted by Django. | Must list deployed hostnames. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins including scheme. | Set for cross-origin admin/form use; otherwise keep same-origin. |
| `DJANGO_TRUST_PROXY_SSL_HEADER` | Trust the reverse proxy's `X-Forwarded-Proto` header. | Set `true` only if the trusted proxy removes client-supplied versions and sets this header itself. |
| `DJANGO_SECURE_SSL_REDIRECT` | Redirect HTTP requests to HTTPS. | `true` behind the TLS proxy. |
| `DJANGO_SECURE_HSTS_SECONDS` | HSTS lifetime in seconds. | Use a reviewed positive value; subdomain/preload behavior is separate. |
| `DJANGO_HSTS_INCLUDE_SUBDOMAINS` | Include all subdomains in HSTS. | Enable only after confirming every subdomain supports HTTPS. |
| `DATABASE_URL` | SQLite or PostgreSQL connection URL. | Must use PostgreSQL. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | Outbound SMTP settings for password reset mail. | Configure a managed SMTP relay and rotate credentials. |
| `DEFAULT_FROM_EMAIL` | Sender address used in outbound mail. | Set a real domain-controlled sender. |
| `DJANGO_TIME_ZONE` | Display timezone for scheduled/date presentation. | Defaults to UTC. Stored timestamps remain UTC. |
| `CURRENCY_LOCALE` | Formatting locale only; does not convert or select a currency. | Choose to match the approved market. |
| `CART_MAX_QUANTITY` | Per-variant cart quantity cap. | Defaults to 10. |
| `SUPPLIER_STOCK_MAX_AGE_HOURS` | Maximum age for a supplier inventory observation. | Set from supplier and fulfillment reliability requirements. |
| `SUPPLIER_SYNC_ENABLED` | Allows an authorized normalized supplier import service to stage catalog snapshots. | Defaults to `false`; enabling it does not configure or authorize a live AliExpress API client. |
| `SUPPLIER_SYNC_STALE_AFTER_SECONDS` | Age after which a stalled supplier sync run may be marked failed before a new run starts. | Defaults to 3600 seconds; keep longer than the maximum bounded adapter request duration. |
| `PAYMENT_ENVIRONMENT` | Selects provider sandbox or production endpoints. | Defaults to `sandbox`; verify keys match before any production use. |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Stripe server key and webhook signing secret. | Keep in a secret manager; use matching test/live credentials. |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` | PayPal server credentials and registered webhook ID. | Keep secrets in a secret manager and match the environment. |
| `POINTS_PROGRAM_ENABLED` | Enables per-unit points posting for paid orders. | Defaults to `false`; enable only after point terms and earning values are approved. |
| `GIVEAWAYS_ENABLED` | Enables public giveaway listing and free-entry pages. | Defaults to `false`; enable only after counsel-reviewed rules, operating controls, and eligibility checks are ready. |
| `REVIEW_RATE_LIMIT_HOURLY` | Maximum review submissions or edits per customer in a rolling hour. | Defaults to 5; must be a positive integer. |
| `REVIEW_REPORT_RATE_LIMIT_HOURLY` | Maximum review reports per customer in a rolling hour. | Defaults to 10; must be a positive integer and requires approved abuse policy before launch. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Local Compose PostgreSQL setup. | Local-only values; use managed database credentials in production. |
