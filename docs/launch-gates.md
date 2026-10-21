# Launch gates and open owner decisions

The current build is a verified foundation, not a production-ready store. Before accepting orders, resolve and verify:

1. Final brand/trademark and approved product imagery/content.
2. Target markets, currency handling and rounding, supplier conversion source, shipping, taxes, delivery estimates, and returns/cancellations.
3. Authorized AliExpress API product, permitted commercial use, credentials, and contract details.
4. Stripe and PayPal merchant/sandbox credentials, supported currencies, webhook secrets, refunds, and reconciliation.
5. Points qualification, pending/finalization timing, expiry/redemption, refund/chargeback reversal, and manual adjustment approval.
6. Giveaway jurisdiction, eligibility verification, no-purchase entry requirements where applicable, official rules, and qualified legal review. Keep `GIVEAWAYS_ENABLED=false` until the review is complete and operating owners are named.
7. Production host, trusted TLS proxy, database/storage encryption, backup retention, restore rehearsal, log retention, monitoring/alerts, and incident owner.
8. Email verification, abuse/rate limits, approved privacy/retention/deletion workflows, legal review of the current customer data-export scope, and legal documents for actual markets.
9. Review terms, content retention, appeal and abuse-reporting process, moderation staffing, and any incentive disclosures.
10. Accessibility review, representative Lighthouse/performance measurement, and browser/device coverage.

No live payments, supplier synchronization, giveaway, tax compliance, production deployment, or production-readiness claim has been verified. Giveaway configuration, free-entry, and drawing workflows exist in code but remain disabled pending legal and operational approval.
