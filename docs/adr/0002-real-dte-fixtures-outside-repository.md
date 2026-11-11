# 0002. Real DTE fixtures stay outside the repository

Date: 2026-11-11

## Status

Accepted

## Context

DTE accepted by the SII are the best oracle for XSD, TED and XMLDSig validation, but
they contain real third-party fiscal data. CAF files and certificates are signing
credentials. None of them may be published.

## Decision

- Tests that use real documents read them from the folder named by the
  `L10N_CL_DTE_REAL_XML_DIR` environment variable and skip when it is not set. They are
  tagged `l10n_cl_dte_real_xml`.
- Committed tests use synthetic data only.
- `.gitignore` excludes PKCS#12 bundles, PEM keys and certificates, and SII CAF
  downloads.

## Consequences

- CI stays green without fiscal data; maintainers run the real-document checks locally
  before releases.
- A skipped real-document test is not evidence. Releases record which real document
  types were checked.
