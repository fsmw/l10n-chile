Prints the export data of Chilean export DTEs 110, 111 and 112 on the
invoice PDF: transaction currency and total, the exchange rate and CLP
total of OtraMoneda, recipient and destination countries, customs
codes (sale mode, sale clause, transport, ports), weights, packages and
the supporting references.

All values come from the immutable snapshot of the issued DTE, so the
PDF matches the signed XML even if the invoice inputs or catalogs change
later. The SII stamp is printed by `l10n_cl_dte_report`.
