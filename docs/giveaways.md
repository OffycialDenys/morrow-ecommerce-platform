# Giveaway controls

Giveaways are separate from the points ledger. The implementation stores campaign rules, individual immutable entry tickets, campaign audit events, independent drawing approval, and a draw record. `GIVEAWAYS_ENABLED` defaults to `false`; the public index, detail, free-entry, activation, entry issuance, and drawing paths remain unavailable until the flag is deliberately enabled.

## Administration and safeguards

Create a draft campaign in Django Admin with an entry method, time window, eligible regions, eligibility criteria, entry calculation, per-customer limits, prize details, HTTPS official-rules URL, disclosures, and a legal-review reference. A second staff member with `promotions.approve_giveaway_rules` must approve the rules. Any rule revision requires returning an approved campaign to draft and obtaining a new approval. The approver cannot activate the campaign; activation also requires the feature flag and the approved time window.

The public site exposes only active, currently open campaigns whose rules still match the approved digest and whose entry method is free. A signed-in customer may submit a region declaration; the service enforces the configured region and entry limits. The region is self-declared and is not independently verified by the current account model.

After the campaign ends, a staff member closes it. A separate staff member approves the frozen entry-pool digest; a third person performs the draw. The draw ranks ticket IDs using an HMAC-SHA256 ordering from a 256-bit `secrets` seed, enforces the approved prize-per-customer limit, and stores the participant digest, seed and seed digest, winner ticket IDs, operators, and algorithm. Draw, entry, approval, and configuration history cannot be edited or deleted through model operations or Django Admin.

## Not connected or verified

The legal-review reference is an auditable staff-supplied reference, not proof that counsel approved the rules. A qualified review and owner approval are still required for each real promotion. Purchase and points entry sources have no payment/points hooks, the customer flow currently supports only free-entry campaigns, entry regions are self-declared, and campaign start/close scheduling and winner notifications are not implemented. There are no real campaign records, legal rules, provider prizes, or production draws in this repository.

Do not activate a live giveaway until the applicable jurisdictions, qualifying methods, free-entry requirements, official rules, disclosures, eligibility verification, privacy/retention handling, support process, and drawing procedure have been reviewed and approved by qualified counsel and the business owner.
