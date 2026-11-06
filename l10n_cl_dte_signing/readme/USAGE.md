Create one signing profile for each company and SII environment. Select a
company-owned certificate with a private key; a certificate may belong to only
one profile. DTE signing resolves the profile from the DTE's immutable mode and
fails closed when no matching profile is active. A DTE manager may create a
synthetic Demo profile and disposable certificate from the company's Demo
configuration; the generated record is visibly marked synthetic and cannot be
used by Certification or Production.

XMLDSig canonicalization materializes inherited namespace declarations before
signing or verification. This avoids incorrect empty namespace declarations
when canonicalizing nested nodes. Previously generated XML must be
verified again after this correction. Existing fiscal artifacts are preserved;
the update does not rewrite stored signatures or reserve replacement folios.

The authentication signer creates the certificate-signed `getToken` XML for a
numeric Certification seed. It requires a DTE manager and one active,
non-synthetic Certification profile with a valid certificate and private key.
It signs in memory without persisting credentials or making a network request.
Other environments are rejected; obtaining a seed and exchanging the signed
request for a token belongs to the gateway authentication client.
