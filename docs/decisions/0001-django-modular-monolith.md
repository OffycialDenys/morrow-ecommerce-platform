# ADR-0001: Django modular monolith

## Status

Accepted for the initial implementation.

## Context

The repository was empty. The machine provides Python 3.11, and the product needs server-side authentication, relational migrations, SEO-friendly rendering, staff permissions, and a future JSON API. No hosting, team preference, or existing code constrains the stack.

## Decision

Use Django 5.2 LTS with PostgreSQL in deployed environments and server-rendered templates as the primary storefront. Keep catalog, accounts, commerce, promotions, reviews, and integrations as separate Django apps. Use a versioned REST-style JSON API for future mobile clients. Django Admin is the initial back-office surface, protected by staff permissions.

## Alternatives considered

- **Laravel:** also a viable mature choice, but no PHP application exists and the available Python runtime plus Django's integrated auth/admin/migrations make the initial delivery smaller.
- **TypeScript with a server-rendering framework and separate API:** viable, but it would require more decisions and dependencies for auth, ORM, admin, and API contracts before product work could begin.
- **SQLite in production:** rejected because commerce writes, concurrency, operations, and backup requirements need a server database. It remains useful for isolated local tests.
- **Microservices:** rejected for the initial system because no scale or independent-team boundary justifies distributed transactions and operations.

## Consequences

- The first release is a deployable modular monolith and can grow behind stable domain boundaries.
- HTML views and the JSON API share the same server-side services.
- Django 5.2 supports the available Python 3.11 runtime and is an LTS release; pin the newest patch within the supported line when dependencies are locked.
- Admin workflows begin with Django Admin; custom dashboards remain a later requirement if workflow needs demand them.
- Live provider behavior and market-specific rules remain gated on owner credentials and decisions.
