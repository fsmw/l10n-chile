# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Authentication clients bound to the official SII environment hosts."""

from .certification_auth import CertificationAuthentication, ProductionSiiAuthentication

__all__ = ["CertificationAuthentication", "ProductionSiiAuthentication"]
