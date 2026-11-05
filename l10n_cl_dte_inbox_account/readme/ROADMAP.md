# Supported scope and limitations

This delivery supports ordinary net-price CLP DTE 33/34, excluded purchase VAT 19%
and exempt lines, explicit quantities/prices, percentage line discounts, and an
optional source due date. Unsupported economic features are diagnosed without
changing the inbox evidence or its validation state.

Global discounts/charges, gross pricing, extra taxes/withholdings, commissions,
foreign currencies, credit/debit notes and non-billable components are outside
this delivery. Missing quantities/prices are not inferred. XML payment metadata
is informational and never creates payments or reduces the invoice balance.

The addon retains Alpha maturity while its inbox dependency remains Alpha.
Functional local tests do not constitute SII certification. Review actual supplier
XML diversity before extending the conservative supported subset.

Supplier-root mergers with existing bindings require a future assisted migration.
Before uninstalling the addon, export/back up its provenance: Odoo removes addon
fields/tables during uninstall, even when the original inbox and bills survive.
