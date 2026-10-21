# Points ledger

## Current rule and activation gate

The implemented earning formula is **configured points per unit multiplied by purchased quantity**, using the value captured on each immutable order line. Entries are posted only after a verified payment transition marks an order paid. The program is disabled by default with `POINTS_PROGRAM_ENABLED=false`; keep it disabled until the business owner approves the point value schedule, qualifying-payment timing, refund/chargeback treatment, and customer terms. Product variants default to zero points, so no credits are issued without explicit configuration.

The authoritative balance is the sum of immutable ledger entries. A stable event key prevents the same order line from earning twice. Refund or correction handling uses compensating entries that reference the original entry and cannot reverse more than its remaining value. The ledger retains the customer, order, order line, event type, signed points, actor, reason, and timestamp. Customer balances and paginated entry history are owner-scoped.

Manual balance corrections use a two-person workflow. Staff with `promotions.request_point_adjustment` create an immutable request; they cannot request an adjustment to their own account. A different staff member with `promotions.approve_point_adjustment` must approve or reject it. Approval posts the ledger entry and resolves the request in one transaction; rejection requires a reason and creates no ledger entry. Replayed request or approval actions are idempotent. The request, decision, actor, and ledger reference are visible in read-only Django Admin history. Reversals remain a separate compensating-entry operation protected by `promotions.adjust_points`.

## Remaining decisions

- Approve point values and earning terms, including whether payment capture is the correct qualification point.
- Define refund, cancellation, chargeback, expiration, and redemption treatment.
- Assign the request and approval permissions to distinct operational roles and review adjustment evidence regularly.
- Approve customer-facing program terms and privacy/retention requirements.

Points are not a cash balance. No live rewards or giveaway is active. Giveaway rules and legal review remain separate launch gates.
