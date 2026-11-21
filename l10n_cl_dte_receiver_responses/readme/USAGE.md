Signing needs the Certification or Production SII environment and an active
signing profile with a private key for the company.

To answer the reception of a received file, open it in *Incoming DTEs >
Import History* (or open one of its DTEs) and click **Email Reception
Response**.

To send a commercial result or a goods receipt, review the incoming DTE and
click **Email Commercial Result** or **Email Goods Receipt**. Several DTEs can
be answered at once from the list with *Action > Email Exchange Response*; one
response is signed per supplier.

The supplier's address is, in order: the partner's *DTE Exchange Email* (when
`l10n_cl_dte_delivery` is installed), the issuer email declared in the DTE
(`CorreoEmisor`) and the partner's email. It can be corrected on the response
before it is sent. The *Exchange Responses* menu lists every response with its
signed XML, SHA-256 and email status; **Resend** emails the same XML again.

When an ACD, RCD or ERM action is accepted by the SII registry, its form offers
**Email Exchange Response** (ACD: result 0, RCD: result 2, ERM: receipt). To
sign and email it automatically, enable *Email Exchange Responses After SII
Registry* on the company's *Chilean DTE* tab. It is disabled by default and
nothing is emailed automatically without it.
