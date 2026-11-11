# 0001. Shared safe XML parsing

Date: 2026-11-11

## Status

Accepted

## Context

The addons parse SII DTE files, envelopes, CAF files and SII web-service
responses. Most call sites built their own `lxml.etree.XMLParser` with
`resolve_entities=False`, but the flags differed between files and some calls
used lxml's default parser, which resolves entities. A single forgotten flag
exposes the server to XML external entity (XXE) disclosure or to entity
expansion bombs.

## Decision

`l10n_cl_dte_core.tools.xml_safety` is the only entry point for parsing XML in
production code:

- `safe_xml_parser()` forces `resolve_entities=False`, `no_network=True`,
  `load_dtd=False`, `dtd_validation=False` and `huge_tree=False`, and refuses
  any attempt to override them.
- `safe_fromstring()`, `safe_parse()` and `safe_xml_schema()` also reject
  documents whose internal subset declares entities, raising
  `UnsafeXMLError`, a subclass of both `XMLSyntaxError` and `ValueError`.

The CAF loader in `l10n_cl_dte_caf` keeps the standard library parser because
the SII FRMA signature is verified over its byte-exact `ElementTree`
serialization of `DA`. It rejects any `<!DOCTYPE` or `<!ENTITY` before
parsing.

## Consequences

- Reviewers can check XML safety with one search: raw `etree.fromstring`,
  `etree.parse` or `etree.XML` outside tests is a defect.
- Existing `except etree.XMLSyntaxError` handlers keep working.
- Parser options that change formatting, such as `encoding` or
  `remove_blank_text`, remain available.
