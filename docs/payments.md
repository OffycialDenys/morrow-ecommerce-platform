# Payments and checkout gate

There is no Stripe or PayPal integration in the current build. The cart uses server-side catalog prices, but checkout is intentionally closed: target markets, shipping/tax calculation, provider credentials, cancellation/refund rules, and provider webhook URLs are not configured.

Before enabling a payment method, add the official current provider SDK and implement server-created payment requests, stable internal order references, amount/currency validation, idempotency, verified webhooks/API retrieval, separate order/payment/fulfillment states, duplicate/out-of-order event handling, refunds, reconciliation, and sandbox tests. Never mark an order paid from a browser return URL. Never collect or store raw card data.

Stripe and PayPal sandbox success/failure/cancellation/refund tests and merchant configuration are launch requirements. Use of a provider does not by itself establish PCI DSS compliance.
