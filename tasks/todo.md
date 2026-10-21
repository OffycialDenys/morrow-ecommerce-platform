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
- [ ] Complete authorized AliExpress catalog synchronization.
  - Implemented: disabled-by-default normalized adapter contract, bounded fixture-backed staging imports, dry-run validation, immutable snapshots, per-record outcomes, duplicate detection, sync history, and read-only staff views. Live products are not modified.
  - Still needed: selected API product and commercial permission, OAuth/credentials, provider response mapping, stock and currency semantics, retries/checkpointing/scheduling, retail pricing, and reviewed import/publish operations.

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
  - Implemented: configurable per-unit values, immutable order-line snapshots, idempotent paid-order credits, compensating partial/full reversals, private account history, and two-person adjustment requests/approvals with immutable audit events. Program defaults off.
  - Giveaway slice implemented: campaign rules and legal-review reference, separately authorized rule approval/activation, free entry with limits and idempotency, immutable entry and audit records, pool approval, and replayable cryptographic draw evidence. Feature defaults off.
  - Still needed: approved points terms/values, refund/chargeback integration, redemption policy, purchase/points entry hooks, winner notification and scheduled campaign operations, verified eligibility controls, and actual counsel approval.
- [ ] Complete verified-purchase review operations.
  - Implemented: paid-and-delivered order-line eligibility, customer submission/edit flow, one-review-per-line constraint, rolling-hour limit, approved-only public summaries, moderator permission checks, and immutable status-change audit events.
  - Still needed: approved review policy, customer abuse reporting and appeal path, and moderation operations/queue targets.
- [ ] Complete provider setup, deployment/backup rehearsal, security/performance/accessibility checks, and final report.
  - Acceptance: every launch gate has evidence or a named blocker.
  - Verify: security scan, regression suite, Lighthouse methodology, restore rehearsal, and provider sandbox records.
