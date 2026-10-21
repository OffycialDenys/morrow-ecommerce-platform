# Customer account data export

Signed-in customers can download a UTF-8 JSON file from the account page. The file uses `schema_version: 1` and includes an export timestamp. The response streams records in bounded database batches, is scoped to the authenticated account, and is sent with private/no-store and attachment headers.

The current export contains:

- Account email, first and last name, account creation time, and most recent sign-in time.
- Saved product names, slugs, and save times.
- Historical order email, totals and statuses, payment provider/status/amount, and immutable line-item snapshots.
- Points ledger entries and manual points adjustment requests.
- Giveaway entries.
- Product reviews, review submission timestamps, reports the customer filed, and the customer's review appeals.

Amounts remain integer minor units with their currency code. The export excludes passwords, authentication/session material, staff identities and notes, internal payment/webhook event details, provider transaction/session references, internal moderation audit events, and data belonging to other customers. Records are the current retained database state; this endpoint does not retrieve deleted records or create a legal preservation copy.

This is an implementation scope, not a determination of legal coverage. Before launch, approve the data categories, retention periods, deletion and correction handling, identity-verification requirements, and response process for each target market.
