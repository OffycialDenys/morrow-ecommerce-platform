# API v1

The JSON API is rooted at `/api/v1/`. Errors use a consistent body:

```json
{"error": {"code": "VALIDATION_ERROR", "message": "The request could not be processed."}}
```

Catalog endpoints are public and only return published products. Private account, cart, and order endpoints require an authenticated session and enforce ownership server-side. List endpoints must validate filters, allowlist sort fields, and return bounded pages. Monetary fields are integer minor units paired with an ISO currency code. No endpoint accepts client-calculated price, stock, points, payment, or role state.

Initial catalog contract:

- `GET /api/v1/products/?q=&category=&currency=&minPrice=&maxPrice=&sort=&page=` lists published items. Price bounds are decimal major-unit amounts and require a recognized currency; price sorting also requires one currency because values from different currencies are not comparable.
- `GET /api/v1/products/{slug}/` returns a published product and its active variants, with explicit availability.
- `GET /health/live/` reports liveness; `GET /health/ready/` reports generic database readiness without exposing dependency details.

This contract will be expanded alongside implemented features. It does not claim payment or supplier API functionality.
