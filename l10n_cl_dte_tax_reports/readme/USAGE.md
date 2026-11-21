**Configuration**

1. In *Invoicing / Accounting > Configuration > Settings*, section *Chilean
   F29 Worksheet*, set the PPM rate and the proportional factor used for the
   common-use VAT credit (100% by default).
2. Optionally review *Configuration > Accounting > F29 Boxes*. The default
   boxes use the tax report tags of the `l10n_cl` chart:

   - VAT debit: `IVA Debito Fiscal` (documents 33/30, 39/35, 56/55, 61/60).
   - Exempt documents (34/32/41/38) and exports (110/111/112): untaxed amount.
   - VAT credit: `IVA Pagado Compras Recuperables`,
     `IVA Pagado Compras Supermercado`, `IVA Pagado Compras Uso Común`
     (proportional), `Compras Activo Fijo`, `Compras Activo Fijo Uso Común`
     (proportional).
   - Without right to credit: base of `Compras IVA No Recuperable` and
     `Compras de Activo Fijo No Recuperable`.
   - Withholdings: `Retención Total (compras)` (tax `I_RTI`, purchase invoice
     46) and `Retención Segunda Categoría` (fees).

   The community chart has no tax for exempt sales, PPM, the single tax on
   workers' wages or imports (DIN); create a box with your own tags or
   document types if you need them. Boxes may be restricted to one company.

**Usage**

- *Accounting > Reporting > Chile > F29 Worksheet*: choose the company, year
  and month, enter the credit remainder of the previous period and press
  *Compute*. Print the worksheet with *Print PDF*.
- *Accounting > Reporting > Chile > Eight-Column Balance*: choose the company
  and the date range, then *View* (list and pivot, exportable) or
  *Print PDF*.

Only posted entries are used. The F29 worksheet is an aid to fill the form;
check it against the Purchase and Sales Register (RCV) before filing.
