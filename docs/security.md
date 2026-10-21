# Security and privacy notes

## Implemented controls

- Django's built-in password hashers, session authentication, CSRF middleware, password validators, and permission framework are used.
- Email identity is normalized and unique case-insensitively. Customer saves are scoped to the authenticated user.
- Product/search input is validated at request boundaries. Sort fields are allowlisted, queries use the ORM, and public views expose only published products.
- Templates keep autoescaping enabled. Supplier descriptions are stored as plain text, and the application does not fetch customer/admin-provided image URLs server-side.
- Cart contents store only variant identifiers and quantities in the server-side Django session. Prices and availability are re-read from the catalog; unknown/stale supplier stock is rejected.
- Public responses do not include database exception details. Security headers include CSP, frame denial, referrer policy, and Permissions Policy.
- Stripe webhook signatures are verified locally; PayPal webhook signatures are verified through PayPal's API before event normalization. Webhook bodies are size-limited, provider return hosts are allowlisted, and internal errors return generic responses while being logged without request payloads.
- Production settings require an externally supplied secret, enable secure session/CSRF cookies, SSL redirect, HSTS, and compressed manifest static assets.
- Runtime dependencies are pinned in `requirements.txt`; CI runs `pip-audit`.

`manage.py check --deploy` currently reports Django warnings W005 and W021 because HSTS subdomain coverage and preload have not been approved for a real domain/DNS topology. Review those settings before launch; they are not enabled by default against an unknown domain.

## Current limitations

- Authentication rate limiting, email verification, data retention workflows, customer deletion/export, structured audit trails, and a full role matrix are not implemented.
- The local in-memory/static settings are for development. Production must use HTTPS, a reverse proxy that normalizes forwarded-protocol headers, managed secrets, encrypted database/backups, PostgreSQL access controls, and tested restore procedures.
- Images are external HTTPS references only; they are not fetched by the server. Provider permission and an allowed image-host policy remain open.
- PayPal's remote verification call is synchronous and public webhook routes do not have application-level rate limiting; configure ingress rate limits and monitoring before production. Provider credentials and webhook IDs still need to be provisioned.
- No claim of PCI compliance, market privacy compliance, or production readiness is made.
