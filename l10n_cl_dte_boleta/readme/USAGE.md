Post a standard customer invoice in CLP and select Chilean DTE type 39 or 41.
The company must be in Demo mode and have a validated synthetic CAF and signing
profile for the selected type. Type 39 supports 19% VAT and/or exempt lines;
type 41 accepts exempt lines only. Select **Issue DTE** to reserve a folio,
create the boleta TED and XML, sign it, and enqueue it in the durable Demo
gateway. The queue-job runner must be active. Demo TrackIDs and responses are
synthetic and are not SII receipts.

This implementation accepts only ordinary sales and services (`IndServicio=3`)
from posted invoices. POS, periodic or domiciliary services, PDF rendering, and
Certification/Production transport are outside this release. For a document
over the annual 135 UF threshold, a named receiver and payment method are
required; cash payment also requires receiver email and phone. An anonymous
receiver is blocked above the threshold. The CLP threshold uses the UF rate at
December 31 of the previous year; issuance stops if that local rate cannot be
verified.

The signed DTE uses a compatibility schema based on the SII boleta schema
(`EnvioBOLETA_v11.xsd`) published at
<https://www.sii.cl/servicios_online/docs/xml/schema_envio_bol.zip>. The
original SII schema is retained unchanged. The compatibility schema corrects
two contradictory percentage restrictions that prevent schema compilation and
adds the `MedioPago`, `CorreoRecep`, and `TelefonoRecep` elements specified by
the current SII boleta format. The compatibility schema and current format
requirements must be reviewed together when either source changes.
