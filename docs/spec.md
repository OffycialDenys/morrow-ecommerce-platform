# Spec: Custom AliExpress e-commerce platform

## Objective

Build an original, accessible, responsive storefront and secure server-side commerce system. Customers will browse/search products, select variants, manage accounts and saved products, place orders through verified payment providers, view purchase history and points, and submit eligible reviews. Staff will manage catalog publication, supplier sync, orders, customers, reviews, points, and reporting. The system must preserve order snapshots, make retries safe, and never present unverified supplier/payment state as successful.

## Tech stack

- Python 3.11 and Django 5.2 LTS, using Django's server-rendered templates, authentication, ORM, migrations, and permission framework.
- PostgreSQL in deployed environments. SQLite is supported only for local development and isolated automated tests.
- A modular monolith with versioned JSON endpoints under `/api/v1/`; provider-specific logic belongs behind adapters.
- Django Admin is the initial staff interface, with explicit model permissions and audit history; bespoke staff workflows can be added where the built-in interface is insufficient.
- Small, authored CSS and JavaScript; no hosted store builder or theme.

## Commands

- Install: `python -m venv .venv` then `.venv/Scripts/python -m pip install -r requirements.txt` (Windows); `source .venv/bin/activate && python -m pip install -r requirements.txt` (POSIX).
- Configure: copy `.env.example` to `.env` and set a unique `DJANGO_SECRET_KEY` for non-development use.
- Migrate: `python manage.py migrate`.
- Run: `python manage.py runserver` for local development only.
- Test: `python manage.py test`.
- Check: `python manage.py check --deploy` with production settings configured.
- Container: `docker compose up --build`.

## Project structure

`config/` holds settings and URL routing; `accounts/`, `catalog/`, `commerce/`, `reviews/`, `promotions/`, and `integrations/` are domain modules; `storefront/` contains customer-facing views; templates and static assets provide the custom responsive UI; `docs/` contains design, API, operations, and launch documentation; `tasks/` tracks implementation phases.

## Code style

Use small domain services for business calculations, explicit enums/states, decimal-safe integer minor units plus ISO currency codes, typed Python annotations where they clarify public interfaces, Django forms for HTML boundaries, and ORM parameterized queries. Keep templates autoescaped. Example:

```python
def line_total_minor(unit_price_minor: int, quantity: int) -> int:
    if unit_price_minor < 0 or quantity < 1:
        raise ValueError("Price and quantity must be non-negative and positive respectively.")
    return unit_price_minor * quantity
```

## Testing strategy

Use Django `TestCase` for model, service, permission, API, and request/response tests. Test unauthenticated and cross-account access, malformed input, product publication, variant/price changes, duplicate payment/event handling, and point idempotency. Use deterministic provider fakes only at network boundaries and label them as test doubles. Live provider, deployment, accessibility, and performance claims require separate evidence.

## Boundaries

- Always validate client and supplier data at boundaries, calculate prices on the server, preserve historical order amounts, use transactions/unique constraints for business effects, and keep secrets out of version control.
- Require owner decisions or access for supplier permissions, real payment flows, market-specific taxes/shipping, point/reversal policy, giveaway rules, and production deployment.
- Never treat missing supplier data as deletion/out-of-stock, mark orders paid from a browser redirect, publish unvalidated imports automatically, or activate a giveaway without legal approval.

## Success criteria

- The project has repeatable local setup, migrations, an operational PostgreSQL deployment configuration, tests, and documented security/operations procedures.
- Published catalog content can be searched and viewed with explicit variant and availability state.
- Authentication and private data access are server-enforced; saved items and orders are scoped to their owner.
- Server-side cart/pricing and immutable order snapshots are implemented and covered by tests.
- Supplier and payment integrations are described as live only after sandbox/live evidence; missing credentials and permissions are documented as blockers.
- Any unresolved legal, security, data-integrity, or financial launch gate is explicit; the platform is not called production-ready while one remains.

## Open decisions

- Store brand/name and approved product imagery.
- Target countries, default currency, shipping/tax policy, and price-change confirmation policy.
- The authorized AliExpress API and credentials.
- Stripe and PayPal merchant/sandbox configuration and refund/cancellation policy.
- Points qualification and reversal policy, and whether point redemption is in scope.
- Giveaway rules, eligible markets, and legal review.
- Production hosting, backup retention, and operational alert destinations.
