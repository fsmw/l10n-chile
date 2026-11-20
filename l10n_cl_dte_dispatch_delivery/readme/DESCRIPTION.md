Emails an issued Chilean delivery guide (DTE 52) to the customer once the
SII accepts it, with its XML (the receiver's EnvioDTE in Certification and
Production) and its PDF, the same way `l10n_cl_dte_delivery` does for
invoices. Production acceptance queues the delivery automatically; Demo and
Certification guides are only sent with the **Send to Customer** button.
The recipient is the customer's DTE exchange email or its email. Port of the
guide delivery of the SMWLAB module `l10n_cl_edi_delivery_guide`.
