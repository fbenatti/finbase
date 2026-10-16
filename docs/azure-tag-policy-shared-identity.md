# Azure tag policies: shared remediation identity

Generated scripts default to `IDENTITY_MODE=shared`. Provision one user-assigned
managed identity (UAMI) for the destination subscription and grant it **Tag
Contributor** at that subscription before deployment. Supply its complete ARM ID:

```bash
SHARED_IDENTITY_ID=/subscriptions/SUB/resourceGroups/RG/providers/Microsoft.ManagedIdentity/userAssignedIdentities/IDENTITY bash generated-plan.sh
SHARED_IDENTITY_ID=/subscriptions/SUB/resourceGroups/RG/providers/Microsoft.ManagedIdentity/userAssignedIdentities/IDENTITY bash generated-apply.sh
```

Generate the PLAN script through the export's `plan_only=true` option. PLAN makes
read-only calls and must pass the same identity/RBAC preflight as APPLY. For a
PLAN, select the destination subscription in Azure CLI first. Identity lookup,
RBAC failures, missing permissions, and a missing identity ID stop execution
before definitions or assignments change. A shared identity must be authorized
in every destination subscription included in the export. Do not share it across
unrelated tenants or environments.

New assignments use the UAMI; their RBAC is inherited from its single subscription
role grant. Existing system-assigned and user-assigned identities are preserved.
An existing system-assigned identity without Tag Contributor may still require
one additional grant. The script does not automatically migrate identities,
delete role assignments, create a UAMI, or grant its initial subscription role.
The caller needs permission to attach the UAMI to the policy assignment.

`IDENTITY_MODE=system` explicitly enables the legacy per-assignment identity flow.
It consumes additional RBAC grants and should not be used to resume a subscription
that is already at its role assignment limit.

## Resuming the Test deployment

1. Save a manifest of current policy assignments, identities and RBAC grants.
2. Count RBAC entries whose scope is actually within Test, excluding management
   group and other inherited scopes from the CLI's broad listing.
3. For every proposed redundant grant, verify an unconditional grant for the same
   principal and role at an ancestor scope. Keep the covering ancestor. Check its
   PIM/schedule lifetime: a temporary grant cannot replace a permanent child grant.
   Stop on failed or incomplete reads. Re-read both grants immediately before any
   deletion. Record each removed grant for rollback.
4. Reclaim enough quota for the shared UAMI's single subscription role grant,
   any existing incomplete system-assigned assignment, and a small reserve.
5. Provision the UAMI and its grant, and verify the grant is visible.
6. Run the new Test PLAN. Confirm its destination, reusable assignments, identity
   choices, and selectors. The identity change does not change classification
   conditions, tag values or business-rule precedence.
7. Apply and inspect the newly submitted remediations. A submitted deployment,
   completed remediation, and verified resource tags are separate outcomes.
8. Verify tags directly and compare against the rule expectations. Trigger and
   inspect policy evaluation after remediation. Do not interpret zero deployments
   or a missing state as proof of correct tags.

A rollback of RBAC cleanup must restore only recorded grants that remain missing;
stop if the principal, role, scope, or conditions have changed concurrently.
Changing policy definitions or scope consolidation is a separate operation from
sharing the remediation identity.
