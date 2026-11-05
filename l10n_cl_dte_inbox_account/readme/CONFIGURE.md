# Configuration

1. Grant **Incoming DTE Vendor Bill Configuration** to the accounting supervisor
   and **Incoming DTE Vendor Bills** to operators. The operator role includes
   native Billing and DTE read access. It does not grant the DTE manager role.
2. In Accounting settings, select a Chilean CLP company with one-peso rounding,
   enable incoming vendor bills and choose an active LATAM purchase journal.
3. Choose excluded purchase VAT 19% (SII code 14) and an explicitly designated
   exempt purchase tax at 0% (SII code 0). The bridge does not create taxes.
4. Maintain existing suppliers with a valid Chilean RUT and native taxpayer type.
   Missing, archived or ambiguous commercial suppliers block the preview.
5. Configure exact supplier item-code, description or supplier-default mappings
   to existing accounts, taxes and optional products/units. Code takes precedence,
   then exact whitespace-normalized description, then supplier default. Conflicting
   item codes block the operation. Archived mappings are ignored; their keys remain
   reserved so they can be edited or restored without duplicating configuration.

Products are optional. Every fiscal line requires an active company account and a
compatible purchase tax. Source units need an explicit matching unit mapping or
an explicit unit choice in the preview; quantities are never converted.

General company settings retain native Odoo Settings administration permissions.
The bridge configuration role does not grant those global administration rights.
An Odoo administrator with the bridge configuration role saves the company journal,
VAT, exemption and enabled flag together. Mapping managers can maintain mappings
without receiving general Settings administration rights.
