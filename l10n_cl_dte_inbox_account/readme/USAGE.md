# Usage

1. A DTE manager imports, locally validates and reviews a received 33/34.
2. A bridge operator opens **Prepare Vendor Bill**. Review the source amounts,
   diagnostics and accounting choices. Correct missing supplier data separately.
3. Select **Create Draft**, or explicitly choose a matching existing draft and
   associate every source ordinal to one existing invoice line. Existing bill
   economics are not rewritten to force a match.
4. Resolve blocking differences and press **Refresh Preview**, then **Confirm Draft**.
   A changed source, configuration, target or choice requires a fresh preview.
5. Review and post the bill through native Accounting. Before posting, the bridge
   revalidates the source digest, cryptography, fiscal identity, details and totals.

VAT and exempt details must reconcile exactly with the signed CLP source. A
one-peso difference blocks creation/linking and posting: there is no automatic
rounding adjustment. Draft accounting accounts and analytics may be edited while
preserving source economics. Fiscal line ordinals and the binding are server-owned.

The bill links back to its incoming DTE. XML downloads retain the original inbox
permissions. Local schema/signature/TED validation does not establish certificate
trust, commercial acceptance or SII acceptance.

Cancel/reset keeps the binding and reserves the supplier/type/folio identity.
Bound bills cannot be deleted. Repeating a successful confirmation returns the
same authorized bill. Resolve historical duplicates before linking; posted or
cancelled candidates block another bill. Disabling the feature blocks new
operations and retains guards on existing bindings.
