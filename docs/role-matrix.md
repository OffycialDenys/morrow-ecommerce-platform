# Staff role matrix

This matrix maps the permissions already enforced by the application to least-privilege staff roles. It is an operating baseline, not an authorization to create accounts or grant permissions. An owner must approve the roles, identify named holders, and review group membership before any live feature is enabled.

Do not grant Django `delete_*` permissions for financial, supplier-history, review-history, giveaway, or audit models. The application makes most of those records immutable; keeping deletion permissions absent also avoids bypassing the intended operational controls.

| Role | Required permissions | Scope and separation requirements |
| --- | --- | --- |
| Account access administrator | `accounts.view_user`, `accounts.change_user`, Django group-management permissions | Manages staff access only. Keep separate from approval and audit-reader roles where staffing permits. |
| Catalog editor | `catalog.view_category`, `catalog.add_category`, `catalog.change_category`, `catalog.view_product`, `catalog.add_product`, `catalog.change_product`, `catalog.view_productvariant`, `catalog.add_productvariant`, `catalog.change_productvariant` | Curates catalog records and publication state. Does not run supplier imports or approve financial/promotional actions. |
| Supplier import operator | `catalog.run_supplier_sync` | Can start bounded staging imports and see only runs they requested. Does not publish products. |
| Supplier import auditor | `catalog.view_supplier_sync` | Can inspect all supplier staging history. Does not start imports or publish products. |
| Commerce reader | `commerce.view_order`, `commerce.view_orderline`, `commerce.view_paymentattempt`, `commerce.view_paymentevent` | Read-only access to customer orders and payment processing evidence. It does not permit payment-state changes. |
| Review moderator | `reviews.moderate_review` | May resolve review, report, and appeal queues. Every state change requires a reason and creates an immutable domain event. |
| Points adjustment requester | `promotions.request_point_adjustment` | May request an adjustment. Must not also receive points-approval permission. |
| Points adjustment approver | `promotions.approve_point_adjustment` | May approve or reject a different staff member's request. Must not also receive requester permission. |
| Points ledger operator | `promotions.adjust_points` | May create authorized compensating reversals. Keep separate from adjustment approval where staffing permits. |
| Giveaway manager | `promotions.manage_giveaways` | Creates and revises draft campaign rules. Cannot approve its own campaign's rules. |
| Giveaway rules approver | `promotions.approve_giveaway_rules` | Independently approves a campaign's rules. Cannot activate a campaign it approved. |
| Giveaway operator | `promotions.activate_giveaways`, `promotions.draw_giveaway_winners` | Activates, closes, or draws campaigns. A draw operator must be independent of both rules and draw-pool approvers. |
| Giveaway draw-pool approver | `promotions.approve_giveaway_draw` | Independently approves the closed entry pool before drawing. |
| Audit reader | `sitecore.view_auditevent` | Read-only access to cross-domain audit records. Does not grant operational permissions. |

The following roles remain intentionally unbundled: points requester and approver; giveaway manager, rules approver, and operator; draw-pool approver and draw operator; and account access administrator and audit reader. The service layer validates the critical points and giveaway separation rules even if a mistaken group assignment combines permissions, but group memberships should still preserve the separation.

Before launch, approve this matrix for the target organization, provision groups from it, assign named staff, review memberships on a fixed cadence, and record an escalation path for emergency access. Production roles also need a decision on who may view customer order, supplier, and audit information.
