# Implementation plan

## Overview

Build the requested store incrementally as a Django modular monolith. The current implementation starts from an empty Git repository and will stop short of external operations that require credentials, approved provider contracts, business rules, or legal review.

## Architecture decisions

- Django 5.2 LTS on Python 3.11; PostgreSQL for deployed use; SQLite for fast local tests.
- Server-rendered storefront plus versioned JSON API; custom CSS and accessible HTML.
- Server-side business services for money, orders, and points; immutable order/ledger records and idempotency keys.
- Product imports remain pending review; payment is only finalized by a verified provider event/API response.

## Dependency-ordered phases

### Phase 1: Foundation

1. Document the initial audit, spec, stack decision, project layout, and open owner decisions.
2. Create Django settings, environment parsing, custom email identity, migrations, secure defaults, Docker/PostgreSQL configuration, and health checks.
3. Verify a clean setup, migrations, automated tests, and deployment checks.

### Phase 2: Catalog and customer experience

4. Add categories, products, variants, explicit publication and availability, catalog admin, and seeded non-production demo content if useful.
5. Add searchable/filterable/paginated catalog pages and versioned catalog API.
6. Add registration/login/logout, account pages, and server-authorized saved products.

### Phase 3: Commerce foundation

7. Add server-priced session cart, currency consistency, quantity availability rechecks, immutable order snapshots, and owner-scoped order history. (Implemented.)
8. Add payment adapter contracts, Stripe/PayPal create/capture operations, and webhook receipt/idempotency storage; keep customer order placement unavailable until payment and shipping/tax decisions and credentials are supplied. (Adapter and webhook foundation implemented; sandbox/checkout work remains.)

### Phase 4: Community and promotions

9. Add review eligibility/moderation and auditable points ledger/reversal rules after purchase qualification is approved.
10. Add giveaway administration and drawing records, gated by legal approval.

### Phase 5: Providers and launch readiness

11. Implement documented authorized AliExpress mapping/sync after permissions and API selection.
12. Verify Stripe/PayPal sandbox flows and implement refunds/reconciliation after sandbox credentials and business rules arrive. (Server adapters and webhook verification implemented; no sandbox evidence yet.)
13. Complete deployment rehearsal, security/performance/accessibility checks, backup recovery, operations docs, and the final acceptance report.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Supplier API/product access unknown | Live catalog import cannot be verified. | Keep provider contract abstract; do not invent endpoints or claim live sync. |
| Payment rules and keys absent | Real checkout cannot be safely completed or verified. | Build safe catalog/cart/order foundations; gate capture/refund behavior. |
| Markets/currency/shipping/tax unknown | Customer totals and legal disclosures cannot be finalized. | Require one explicit currency per cart and do not estimate tax/shipping. |
| Giveaway legality unknown | Illegal promotion risk. | Keep inactive and require counsel-reviewed rules. |
| Scope spans a full commerce platform | Single-pass code increases defect risk. | Deliver and verify vertical slices; keep task status and blockers current. |

## Checkpoints

- Foundation: install, migrations, Django system checks, tests, and clean startup.
- Catalog/accounts: browse/search/detail, authentication, and wishlist authorization tests.
- Commerce: server-derived totals, currency/availability checks, order ownership, and duplicate request behavior.
- Launch: provider sandbox evidence, security review, deployment run, measured performance, and documented owner decisions.
