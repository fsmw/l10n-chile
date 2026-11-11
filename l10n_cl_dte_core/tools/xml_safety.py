# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Hardened XML parsing shared by every Chilean DTE addon.

SII documents, CAF files and web-service responses never need a DTD. Every
parser built here refuses entity resolution, network access, DTD loading and
huge trees, and every parsed document is rejected when its internal subset
declares entities. This blocks external entity (XXE) disclosure and entity
expansion bombs even when a caller forgets a parser flag.
"""

from lxml import etree

_FORCED_OPTIONS = {
    "resolve_entities": False,
    "no_network": True,
    "load_dtd": False,
    "dtd_validation": False,
    "huge_tree": False,
}
_ALLOWED_OPTIONS = frozenset(
    {
        "encoding",
        "remove_blank_text",
        "remove_comments",
        "remove_pis",
        "strip_cdata",
        "collect_ids",
        "ns_clean",
        "compact",
    }
)


class UnsafeXMLError(etree.XMLSyntaxError, ValueError):
    """Raised when an XML input declares entities.

    It subclasses ``XMLSyntaxError`` and ``ValueError`` so existing handlers
    for malformed XML also reject unsafe documents.
    """


def safe_xml_parser(**options):
    """Return an ``lxml`` parser with the security flags forced.

    Only formatting options are accepted. Passing a security flag raises
    ``TypeError`` so that no caller can weaken the parser.
    """
    unexpected = set(options) - _ALLOWED_OPTIONS
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise TypeError(f"Unsupported XML parser option(s): {names}")
    return etree.XMLParser(**options, **_FORCED_OPTIONS)


def _check_tree(tree):
    dtd = tree.docinfo.internalDTD
    if dtd is not None and next(iter(dtd.iterentities()), None) is not None:
        raise UnsafeXMLError("XML entity declarations are not allowed.", 0, 1, 1)
    return tree


def safe_fromstring(data, **options):
    """Parse ``data`` with :func:`safe_xml_parser` and return the root."""
    root = etree.fromstring(data, parser=safe_xml_parser(**options))
    _check_tree(root.getroottree())
    return root


def safe_parse(source, **options):
    """Parse a file name or file object and return the element tree."""
    return _check_tree(etree.parse(source, parser=safe_xml_parser(**options)))


def safe_xml_schema(source, resolvers=(), **options):
    """Compile an XSD file or file object read through a safe parser."""
    parser = safe_xml_parser(**options)
    for resolver in resolvers:
        parser.resolvers.add(resolver)
    return etree.XMLSchema(_check_tree(etree.parse(source, parser=parser)))
