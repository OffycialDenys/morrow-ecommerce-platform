# AliExpress integration gate

No AliExpress API endpoint or access capability is assumed. Before live implementation, the owner must supply or approve the exact authorized API product, commercial usage permissions, credentials, supported endpoints, pagination/rate limits, product/variant identifiers, image rights, stock/price semantics, currencies, shipping semantics, deletion semantics, and polling requirements.

Until then:

- The catalog stores supplier IDs separately from internal product and variant IDs.
- Supplier-observed stock has its own source and observation timestamp. Missing or stale stock is not treated as zero or as deletion; it is not purchasable.
- Products default to draft and must be explicitly reviewed/published by staff.
- There is no scraper, undocumented endpoint, live sync job, supplier request, or claim that synchronization is operational.

When approved access arrives, implement a provider adapter and idempotent bounded sync job with dry-run, per-record errors, retry policy, last-success tracking, and fixture-backed mapping tests. Do not automatically unpublish/overwrite last-known-good content from an incomplete supplier response.
