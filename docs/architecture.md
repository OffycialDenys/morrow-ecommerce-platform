# Architecture

The platform is a Django modular monolith. Server-rendered HTML is the primary storefront surface for SEO and keeps prices, permissions, and state transitions under server control. A versioned JSON API supports future mobile clients without duplicating domain rules. Database-backed migrations define relational integrity; PostgreSQL is required for deployed use.

## Domain boundaries

- `accounts`: customer identity, profiles, addresses, and saved products.
- `catalog`: categories, products, variants, media references, supplier identifiers, publication, and observed availability.
- `commerce`: cart calculations, order snapshots, payment attempts/events, provider adapters, payment state, and fulfillment state.
- `promotions`: points ledger, reversals, giveaway configuration, entry records, and drawing audit.
- `reviews`: verified-purchase eligibility, review submission, moderation, and rating summaries.
- AliExpress synchronization remains a planned integration boundary until authorized API access and contracts are available.
- `storefront`: presentation and request orchestration; business calculations stay in domain services.
- `config`: environment configuration, URL routing, and production defaults.

The current code implements the catalog, accounts, cart, order snapshots/history, Stripe and PayPal server adapters, payment attempt/event state handling, health, and storefront slices. Provider checkout is not connected to customer order placement because market, shipping, tax, and payment decisions remain open. `promotions` and `reviews` are planned domain boundaries; they do not yet exist as application modules or contain production functionality.

## Initial decisions

- Django 5.2 LTS is selected because the available runtime is Python 3.11 and the framework supplies mature auth, sessions, ORM, migration, and admin components. Django 5.2 supports Python 3.11 and is an LTS release. See [Django 5.2 release notes](https://docs.djangoproject.com/en/5.2/releases/5.2/) and [release index](https://docs.djangoproject.com/en/5.2/releases/).
- PostgreSQL is the production database. SQLite may be used for quick local development, but not as a production database.
- Money is stored as integer minor units and an explicit ISO currency code. Mixed-currency cart totals are rejected until an approved conversion policy exists.
- Supplier listings and purchasable variants are distinct. New imported records remain unpublished until staff review.
- Payments use separate payment and fulfillment states. A browser return cannot finalize payment; verified provider webhooks or API retrieval must do that.
- Giveaway activation is a legal launch gate. Points use immutable ledger entries and compensating reversals.

## Trust boundaries

Browser input, supplier responses, payment callbacks, environment configuration, and staff actions are untrusted until validated/authorized. ORM queries remain parameterized. Django template autoescaping is kept enabled. External image references must use HTTPS and are not fetched server-side by the application. Payment card data is never stored.

## Operational shape

Local development uses SQLite by default. Docker Compose runs the application with PostgreSQL for integration testing. Production uses a WSGI server behind a TLS-terminating reverse proxy, persistent managed PostgreSQL, managed secrets, backups, and a separately supervised worker/scheduler when those workloads are implemented.
