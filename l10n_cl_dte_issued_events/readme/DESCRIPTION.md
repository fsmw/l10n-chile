Tracks how receivers answer the DTEs the company issues, without ever
sending a response or writing accounting entries.

- **SII registry events (Law 20.956).** For invoices (33, 34, 43 and 46)
  accepted by SII in Certification or Production, the module lists the
  events of the SII acceptance and claim registry (`listarEventosHistDoc`):
  ACD, ERM, RCD, RFP, RFT, PAG, CED, NCA, ENC and any other code SII
  returns. Each event is stored once per document, source, code and event
  date.
- **Emailed responses.** When the DTE intake mailbox of `l10n_cl_dte_inbox`
  receives a `RespuestaDTE` (RecepcionEnvio or ResultadoDTE) or an
  `EnvioRecibos` (Law 19.983 receipt of goods) that refers to one of the
  company's issued DTEs, its XML signatures are verified and the file is kept
  as evidence linked to the document. Files with an invalid signature are
  refused; verified files that match none of our DTEs are kept and flagged.
- **Receiver state.** The document and its invoice show a derived state:
  accepted, goods received, content claimed, partial or total lack of goods
  claimed, paid, assigned, rejected by email, awaiting receiver or tacitly
  accepted once the eight calendar days after SII reception pass without a
  claim. SII registry events take precedence over emailed answers. A claim
  schedules a to-do activity for the invoice salesperson.
