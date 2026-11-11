# 0003. PKCS#12 passwords are never stored on wizards

Date: 2026-11-11

## Status

Accepted

## Context

Odoo's `certificate.certificate` keeps `pkcs12_password` because it decrypts
the bundle later. Upload wizards are transient models: a stored password field
writes a second copy to the database, which survives a failed upload until the
transient vacuum runs.

## Decision

Wizard password fields are non-stored computed fields with an inverse. The
inverse passes the value straight to `certificate.certificate` while the
dialog is saved. Access checks run before the certificate is created.

## Consequences

- No wizard table holds a password column.
- Opening a certificate happens when the dialog is saved, which in Odoo is the
  same request that triggers the wizard button.
