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
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Local Compose PostgreSQL setup. | Local-only values; use managed database credentials in production. |
