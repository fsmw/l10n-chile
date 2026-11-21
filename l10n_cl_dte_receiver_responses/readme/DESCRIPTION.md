This module sends the receiver's own exchange responses for the supplier DTEs
registered by `l10n_cl_dte_inbox`. Since Law 20.956 the legal acceptance or
claim of a DTE is the SII registry action (ACD, RCD, ERM, RFP, RFT); the
emailed XML files below are the commercial exchange that the SII format still
defines and that suppliers' systems expect.

- **RespuestaDTE with RecepcionEnvio**: answers the reception of a received
  file, with the envelope status (received, schema error, signature error,
  wrong receiver) and the reception status of each DTE in it.
- **RespuestaDTE with ResultadoDTE**: the commercial result of each DTE:
  0 accepted, 1 accepted with objections, 2 rejected, with a reason.
- **EnvioRecibos**: one Law 19.983 receipt of goods or services per DTE, with
  the receipt place (Recinto), the signer's RUT and the legal declaration.

Every response is signed with the company's signing certificate (XMLDSig
RSA-SHA1 over the referenced element, inclusive C14N), validated against the
official SII schemas bundled in `static/xsd` and verified before it is stored.
The exact signed XML is kept as evidence with its SHA-256 and emailed to the
supplier's DTE exchange address. Each kind of response is prepared once per
DTE (once per received file for receptions); emailing it again is an explicit
"Resend" of the same XML.
