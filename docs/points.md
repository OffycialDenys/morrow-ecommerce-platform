# Points ledger

## Current rule and activation gate

The implemented earning formula is **configured points per unit multiplied by purchased quantity**, using the value captured on each immutable order line. Entries are posted only after a verified payment transition marks an order paid. The program is disabled by default with `POINTS_PROGRAM_ENABLED=false`; keep it disabled until the business owner approves the point value schedule, qualifying-payment timing, refund/chargeback treatment, and customer terms. Product variants default to zero points, so no credits are issued without explicit configuration.

The authoritative balance is the sum of immutable ledger entries. A stable event key prevents the same order line from earning twice. Refund or correction handling uses compensating entries that reference the original entry and cannot reverse more than its remaining value. The ledger retains the customer, order, order line, event type, signed points, actor, reason, and timestamp. Customer balances and paginated entry history are owner-scoped.

Manual adjustments require the dedicated `promotions.adjust_points` permission and a reason. The ledger itself is read-only in Django Admin; an approval/request screen and dual-control workflow are still outstanding. Point redemption and projected cart points are not implemented.

## Remaining decisions

- Approve point values and earning terms, including whether payment capture is the correct qualification point.
- Define refund, cancellation, chargeback, expiration, and redemption treatment.
- Decide who may request and approve adjustments and whether a second staff approver is required.
- Approve customer-facing program terms and privacy/retention requirements.

Points are not a cash balance. No live rewards or giveaway is active. Giveaway rules and legal review remain separate launch gates.
