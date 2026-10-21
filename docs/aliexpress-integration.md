# AliExpress integration gate

No AliExpress API endpoint or access capability is assumed. Before live implementation, the owner must supply or approve the exact authorized API product, commercial usage permissions, credentials, supported endpoints, pagination/rate limits, product/variant identifiers, image rights, stock/price semantics, currencies, shipping semantics, deletion semantics, and polling requirements. The official Open Platform documentation says seller business-data access requires seller authorization, and API permission groups can require separate approval ([seller authorization](https://developer.alibaba.com/docs/doc.htm?articleId=120687&docType=1&treeId=727), [API permission groups](https://developer.alibaba.com/docs/doc.htm?articleId=120676&docType=1&treeId=727)). These requirements do not establish which API product is appropriate for this store.

## Implemented staging boundary

- `integrations.suppliers` defines a normalized, typed adapter contract. A batch is capped at 100 products and validates text, HTTPS image references, currency codes, minor-unit supplier prices, variant identifiers, and bounded attributes.
- Authorized imports can create immutable supplier snapshots and per-record outcomes. Repeated identical IDs within one batch are skipped; conflicting duplicates and invalid records are reported without replacing earlier snapshots.
- Dry runs validate records without storing product snapshots. Supplier data is staging-only: imports never create or update public `Product` or `ProductVariant` records, calculate retail prices, fetch images, infer stock, or unpublish catalog items.
- Run history and errors are read-only in Django Admin. Imports require `catalog.run_supplier_sync` and `SUPPLIER_SYNC_ENABLED=true`; the feature flag defaults to `false`.
- Fixture tests cover the normalized contract, not AliExpress's raw response format. They do not prove API access, commercial permission, or live synchronization.

## Still gated

There is no live AliExpress client, OAuth flow, endpoint, schedule, retry policy, checkpoint/resume path, inventory mapping, retail pricing rule, or approved importer from staged supplier data. The owner must approve the provider product and product/variant data semantics before a real adapter can map responses to the normalized contract. Verify image rights, product cost/currency/shipping meanings, permitted commercial use, deletion behavior, and API limits with the selected provider account. Never treat missing data as deletion or out-of-stock, and do not publish supplier data automatically.
