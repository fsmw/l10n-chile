Only a DTE manager may load CAF files and configure the SII public trust keys.
Validation checks the CAF structure, issuer and folio metadata, the SII signature
over DA, and the embedded RSA key pair. It refuses CAF files whose saved bytes
no longer match their registered SHA-256 digest.

Validated CAF ranges may overlap. The folio ledger enforces uniqueness by
company, DTE type, and folio number, records the source CAF as DTE evidence, and
never recycles a reserved or voided folio. Reservation accepts an idempotency
key so a repeated request returns the same ledger entry.

The trust-key records must be populated with the appropriate official SII
public keys and their validity dates before validating production CAF files.
Local tests use disposable synthetic keys and prove the verification contract;
they do not certify compatibility with an SII-issued CAF or establish SII
acceptance.
