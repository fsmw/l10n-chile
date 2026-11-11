# 0004. CAF FRMA is not verified locally

Date: 2026-11-11

## Status

Accepted

## Context

A CAF (Código de Autorización de Folios) is the SII authorization that lets an issuer
stamp DTE with a folio range. Its `FRMA` element is the SII signature over the `DA`
section, made with the SII private key. Verifying `FRMA` against the SII public key
proves that the SII issued the authorization.

`l10n_cl_dte_caf` already contains the verification path through trust keys
(`l10n_cl.caf.trust.key`). Without an applicable trust key the result is
`key_unavailable`, and the CAF is still accepted. So the default behaviour does not
verify `FRMA`.

Other implementations in the workspace take the same approach: the certificate is loaded
for signing and `FRMA` is not checked locally.

## Decision

The team accepts the risk of not verifying `FRMA` locally. Specifically:

- A CAF is accepted when it is well formed, its `RSAPK` and `RSASK` match, and its folio
  range is consistent. These checks stay mandatory.
- The local `FRMA` check is not required for import and is not a blocker for
  certification or production.
- A CAF without a verified `FRMA` keeps `sii_signature_status = key_unavailable` and
  must be shown as unverified in the interface. It must never be reported as verified.
- CAF files are obtained only from the SII through the authenticated download flow, and
  they are stored outside the repository (see ADR 0002 and the `.gitignore` rules that
  exclude `CAF*.xml`).

## Consequences

- A tampered or forged CAF that passes the structural checks would not be caught
  locally. The main mitigation is the authenticated download flow, not a cryptographic
  check.
- The `FRMA` verification test is removed from the Phase 6 plan. The existing trust-key
  mechanism stays available, so verification can be enabled later by loading the SII
  public key as a trust key, with no code change.
- This decision must be revisited if CAF files start arriving from any source other than
  the SII download, or before a regulatory audit that requires local signature
  verification.
