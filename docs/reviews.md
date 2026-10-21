# Verified purchase reviews

Customers can submit one review for each order line after payment is confirmed and fulfillment is marked delivered. Eligibility is derived from the order and the signed-in customer; the form does not accept a product, customer, order, or moderation state from the browser.

Each review contains a 1–5 rating and 20–2,000 characters of text. New reviews wait in moderation. Editing an existing review sends it back to the pending state, so a change to already-published text is reviewed again. Hidden reviews cannot be edited through the customer flow. Public product pages show only approved reviews and do not display account identity.

The admin action requires the `reviews.moderate_review` permission and a reason. Every status transition creates a protected, immutable moderation event recording the previous and new states, moderator, reason, and time. Successful submissions and edits are recorded separately so the hourly limit counts repeated edits to one review. Customers are limited to five review submissions or edits per rolling hour by default; operators can change this with `REVIEW_RATE_LIMIT_HOURLY`.

## Launch policy still needed

The current limits and moderation behavior are conservative implementation defaults, not approved legal or customer policy. Before launch, approve review terms, retention and appeal procedures, abuse reporting and repeat-abuse handling, moderation staffing, and any disclosure rules for incentives or supplier relationships. There is no customer report-abuse flow, moderator queue SLA, bulk moderation, or giveaway review incentive system yet.
