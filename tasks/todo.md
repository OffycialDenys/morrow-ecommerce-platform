# Implementation tasks

## Foundation

- [x] Bootstrap Django 5.2 LTS, configuration, custom email identity, and PostgreSQL-capable settings.
  - Acceptance: app boots with environment-driven secrets and database; no credentials are hardcoded.
  - Verify: migrate, `manage.py check`, and auth tests.
- [x] Add development/prod Docker configuration, health checks, CI, environment template, and setup docs.
  - Acceptance: Docker uses PostgreSQL, production has secure defaults, and setup is reproducible.
  - Verify: compose config/build/start where Docker daemon permits; deployment check.

## Catalog and accounts

- [x] Add catalog entities, publication controls, admin, safe catalog API, and search/filter/pagination.
  - Acceptance: only published products appear; variant prices are minor-unit integers; API filters/sorts are allowlisted.
  - Verify: model/view/API tests, including malformed filters and unpublished products.
- [x] Add account registration/login/logout and owner-scoped saved products.
  - Acceptance: email is unique; customers cannot modify/read another customer's saved data.
  - Verify: auth, CSRF, and cross-account request tests.

## Commerce

- [x] Add server-side cart calculations with variant availability and currency checks.
  - Acceptance: client-provided prices are ignored; stale/unavailable items block checkout review.
  - Verify: forged price, quantity, stock, unavailable variant, and mixed-currency tests.
- [x] Add durable order snapshots and owner-scoped order history.
  - Acceptance: historical totals stay fixed; payment and fulfillment states are distinct.
  - Verify: snapshot, authorization, and duplicate order tests. Payment-event and state-transition tests remain with provider work.
- [ ] Complete provider sandbox flows, refund/reconciliation operations, and customer checkout route.
  - Acceptance: exact current provider contract is verified and sandbox tests run; no browser redirect marks an order paid.
  - Implemented: Stripe Checkout session creation, PayPal Orders v2 create/capture, signature verification, bounded webhook endpoints, amount checks, provider transaction references, and duplicate/out-of-order event handling.
  - Still needed: market/shipping/tax rules, customer return/capture path, refund records and authorization, provider reconciliation, merchant credentials/webhook configuration, and real sandbox evidence.

## Promotions, reviews, and launch

- [ ] Complete points administration and giveaway system.
  - Acceptance: repeated qualification events do not double-credit; refunds use compensating entries.
  - Implemented: configurable per-unit values, immutable order-line snapshots, idempotent paid-order credits, compensating partial/full reversals, permission-checked adjustments, and private paginated account history. Program defaults off.
  - Still needed: approved terms/values, refund/chargeback integration, adjustment request/approval UI, redemption policy, giveaway configuration/entries/auditable draw, and legal review.
- [ ] Add verified-purchase review moderation and giveaway administration.
  - Acceptance: review eligibility derives from order data; giveaway remains inactive until legal approval.
  - Verify: permission, eligibility, audit, and draw tests.
- [ ] Complete provider setup, deployment/backup rehearsal, security/performance/accessibility checks, and final report.
  - Acceptance: every launch gate has evidence or a named blocker.
  - Verify: security scan, regression suite, Lighthouse methodology, restore rehearsal, and provider sandbox records.
