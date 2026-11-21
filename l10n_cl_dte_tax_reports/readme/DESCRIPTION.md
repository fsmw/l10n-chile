Chilean tax reports built on plain community accounting (no Enterprise
`account_reports`):

- **Balance de 8 columnas** (eight-column balance) for a company and a date
  range. For each account it shows debits, credits, debit balance, credit
  balance, assets, liabilities, losses and gains. Accounts are classified by
  their community account type: asset, liability and equity accounts go to the
  assets/liabilities columns and accumulate every posted entry up to the end
  date; income and expense accounts go to the losses/gains columns and only
  include the entries of the range. Results of earlier periods and the
  current-year earnings accounts are grouped in one *unallocated earnings*
  line, and a *profit/loss for the period* line balances both column pairs.
  It can be printed as PDF or opened as a list/pivot (exportable to
  spreadsheet with the standard export).
- **F29 worksheet** for a month: number of documents and VAT debits by
  document class (invoices, receipts, debit and credit notes, exempt documents
  and exports), VAT credits on purchases (recoverable, common use with the
  proportional factor, fixed assets, debit and credit notes received), the
  net amount without right to credit, VAT withheld on purchase invoices
  (total retention), second category withholdings, PPM and the resulting VAT
  payable or remaining credit for the next period. Each line shows the
  related SII F29 box codes.

Every F29 line is a configurable *F29 box* that reads the tax report tags of
the Chilean chart of accounts (`l10n_cl`) on tax or base lines, or the untaxed
amount of the documents, filtered by document type.
