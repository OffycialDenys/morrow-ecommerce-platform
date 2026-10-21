# Initial engineering assessment

## Repository findings

- The workspace was an empty Git repository on `master`; it had no commits, application code, tests, manifests, documentation, migrations, or deployment files.
- There was no existing behavior to preserve and no baseline test or build command to run.
- The machine has Python 3.11.5, Node.js 22.19.0, PHP, Composer, and Docker CLI. Python is the selected runtime because Django provides server-side authentication, sessions, ORM, migrations, forms, and staff permissions in one maintainable application.
- Django 5.2 is an LTS release and supports Python 3.11. PostgreSQL will be the deployment database; SQLite is limited to local development and isolated tests.

## Initial risk and gap assessment

| Area | Finding | Priority |
|---|---|---:|
| Architecture | No application or architecture existed. | P0 |
| Functionality | Catalog, accounts, cart, orders, payments, points, reviews, administration, and APIs were all missing. | P0 |
| Security | No application security controls or secret configuration existed. | P0 |
| Data integrity | No schema, migration strategy, or audit trail existed. | P0 |
| Integrations | No AliExpress approval/credentials, Stripe or PayPal credentials, or provider configuration were present. | P1 |
| Business rules | Market, currency, shipping/tax policy, point qualification, refund rules, and giveaway terms are unspecified. | P1 |
| Operations | No deployment, health checks, monitoring, backup, or recovery guidance existed. | P1 |
| Performance | No implementation exists to measure. The two-second and Lighthouse targets cannot yet be asserted. | P2 |

## Prioritized execution plan

1. Establish a documented Django modular monolith, PostgreSQL deployment configuration, environment handling, security defaults, and automated checks.
2. Build the catalog, searchable product pages, email-based customer accounts, and saved products with explicit product publication state.
3. Add a server-priced cart and durable order snapshots; keep checkout/payment transitions disabled until approved shipping/tax/currency rules and provider credentials are available.
4. Add the points ledger, verified-purchase review moderation, and administrative workflows after the order/payment rules are agreed.
5. Add a documented AliExpress adapter only against approved, verified API contracts; then integrate Stripe and PayPal using current official SDKs and sandbox credentials.
6. Complete operational hardening, security review, performance measurement, and launch-gate documentation.

## Initial blockers

- Live product sync is blocked on the exact authorized AliExpress API/product, commercial permissions, and credentials.
- Payment sandbox verification is blocked on Stripe and PayPal sandbox credentials and business decisions about currency, shipping, taxes, cancellations, and refunds.
- A live giveaway is blocked on approved rules and qualified legal review.
- Production performance and deployment claims are blocked on a representative production-like deployment and target device/network conditions.
