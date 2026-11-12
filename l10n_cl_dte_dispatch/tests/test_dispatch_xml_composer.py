# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import (
    safe_fromstring,
)

from .common import DispatchTestCommon

SII = "{http://www.sii.cl/SiiDte}"
ISSUER = {
    "vat": "76123456-7",
    "name": "Empresa GD SpA",
    "activity": "Comercio",
    "activity_code": "471100",
    "address": "Calle 1",
    "commune": "Santiago",
    "city": "Santiago",
}
RECEIVER = {
    "vat": "66666666-6",
    "name": "Cliente GD",
    "activity": "Comercio",
    "address": "Destino 456",
    "commune": "Providencia",
    "city": "Santiago",
}


@tagged("post_install", "-at_install")
class TestDispatchXmlComposer(DispatchTestCommon):
    def setUp(self):
        super().setUp()
        self.composer = self.env["l10n_cl.dte.dispatch.xml.composer"]

    def _mixed_dispatch(self):
        dispatch = self._create_dispatch()
        self._create_line(dispatch, quantity=2.0, unit_price=1000.0)
        self._create_line(
            dispatch, quantity=1.0, unit_price=500.0, tax_category="exempt"
        )
        self._create_line(
            dispatch, quantity=3.0, unit_price=0.0, tax_category="unvalued"
        )
        return dispatch

    def _compose(self, dispatch, **kwargs):
        return self.composer.compose_unsigned_guide(
            dispatch, 1, ISSUER, RECEIVER, "2026-11-12T10:00:00", **kwargs
        )

    def test_totals_split_net_exempt_and_vat(self):
        # Arrange
        root = safe_fromstring(self._compose(self._mixed_dispatch()))
        totals = root.find(f".//{SII}Totales")
        # Act / Assert
        self.assertEqual(totals.findtext(f"{SII}MntNeto"), "2000")
        self.assertEqual(totals.findtext(f"{SII}MntExe"), "500")
        self.assertEqual(totals.findtext(f"{SII}IVA"), "380")
        self.assertEqual(totals.findtext(f"{SII}MntTotal"), "2880")

    def test_unvalued_line_is_flagged_and_adds_no_amount(self):
        # Arrange
        root = safe_fromstring(self._compose(self._mixed_dispatch()))
        detail = root.findall(f".//{SII}Detalle")[2]
        # Act / Assert
        self.assertEqual(detail.findtext(f"{SII}IndExe"), "2")
        self.assertEqual(detail.findtext(f"{SII}MontoItem"), "0")

    def test_transport_block_is_only_added_when_given(self):
        # Arrange
        dispatch = self._mixed_dispatch()
        # Act
        without = safe_fromstring(self._compose(dispatch))
        with_transport = safe_fromstring(
            self._compose(dispatch, transport={"vehicle_plate": "ABCD12"})
        )
        # Assert
        self.assertIsNone(without.find(f".//{SII}Transporte"))
        transport = with_transport.find(f".//{SII}Receptor/{SII}Transporte")
        self.assertEqual(transport.findtext(f"{SII}Patente"), "ABCD12")

    def test_snapshot_serialization_matches_the_record_composer(self):
        # Arrange
        dispatch = self._mixed_dispatch()
        composer = self.composer
        # Act
        snapshot = composer.build_snapshot(
            dispatch, 1, ISSUER, RECEIVER, "2026-11-12T10:00:00"
        )
        from_snapshot = composer.serialize_unsigned_guide(snapshot)
        from_record = self._compose(dispatch)
        # Assert
        self.assertEqual(from_snapshot, from_record)
        self.assertEqual(snapshot["totals"]["total"], 2880)
