# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""SII VAT retention codes a purchase invoice (DTE 45/46) may declare.

Source: SII "Formato Documentos Tributarios Electrónicos", version 2.5
(2026-02), section 4 "Codificación tipos de impuestos y recargos". Only the
codes that table lists for "Factura de Compra emitida cod (45, 46)" with a
stated retention rate are supported. Codes 35, 44 and 45 in the same range are
specific or additional taxes, 40, 42 and 43 are no longer listed, 46 (gold)
applies only to sale invoices 30/33 and 49 (0%) is reserved to the Bolsa de
Productos; the cambio de sujeto codes (301, 321, ...) belong to withholding
agents. All of them are refused.
"""

# Total VAT retention (Retención Total IVA).
TOTAL_RETENTION_CODE = 15
VAT_RATE = 19

# SII code -> retention rate (percent of the net amount of the line).
VAT_RETENTION_RATES = {
    TOTAL_RETENTION_CODE: 19,
    30: 10,  # IVA retenido legumbres
    31: 19,  # IVA retenido silvestres (total)
    32: 8,  # IVA retenido ganado
    33: 8,  # IVA retenido madera
    34: 4,  # IVA retenido trigo
    36: 10,  # IVA retenido arroz
    37: 10,  # IVA retenido hidrobiológicos
    38: 19,  # IVA retenido chatarra (total)
    39: 19,  # IVA retenido PPA (total)
    41: 19,  # IVA retenido construcción (total)
    47: 19,  # IVA retenido cartones (total)
    48: 14,  # IVA retenido frambuesas
}

# Product VAT retention codes of the SII table, supported or not; 35, 44 and
# 45 share the range but are specific or additional taxes.
PRODUCT_RETENTION_CODE_RANGE = frozenset(range(30, 50)) - {35, 44, 45}


def is_retention_code(code):
    """Whether an SII code belongs to the VAT retention family (15, 30-49)."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    return code == TOTAL_RETENTION_CODE or code in PRODUCT_RETENTION_CODE_RANGE


def official_retention_rate(code):
    """Return the official rate of a supported retention code, else None."""
    try:
        return VAT_RETENTION_RATES.get(int(code))
    except (TypeError, ValueError):
        return None
