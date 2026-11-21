SII exchange response schema source
===================================

The five XSD files in this directory were extracted from two ZIP files that
the SII links from its "Formato XML de Documentos Electrónicos" page,
https://www.sii.cl/servicios_online/3532-formato_xml-3811.html:

* ``schema_ic.zip`` ("Bajar schema XML de Intercambio entre Contribuyentes"),
  https://www.sii.cl/factura_electronica/factura_mercado/schema_ic.zip. It
  contains only ``RespuestaEnvioDTE_v10.xsd``.
* ``schema19983.zip`` ("Bajar schema XML de Recibo ley 19.983"),
  https://www.sii.cl/factura_electronica/factura_mercado/schema19983.zip, also
  linked by https://www.sii.cl/factura_electronica/form_ele.htm. It contains
  ``EnvioRecibos_v10.xsd``, ``Recibos_v10.xsd``, ``SiiTypes_v10.xsd`` and
  ``xmldsignature_v10.xsd``.

``RespuestaEnvioDTE_v10.xsd`` includes ``SiiTypes_v10.xsd`` and imports
``xmldsignature_v10.xsd`` without shipping them; it resolves them against the
copies from ``schema19983.zip``. ``EnvioRecibos_v10.xsd`` includes
``Recibos_v10.xsd``, which includes the other two files.

Retrieved 2026-11-21. Line endings were converted from CRLF to LF and
trailing whitespace was removed, as the repository's pre-commit hooks require.
The parsed schema content is unchanged.

SHA-256 of the original ZIP files:

* ``schema_ic.zip``: ``a97ec9160341b276b5fe07330de72779d90d3d514fd33b40ba4028dc9e0f3f0f``
* ``schema19983.zip``: ``61b9e7753fa14df95d094467942964aeb9f55e963429d03cad082fc07f22f577``

SHA-256 of the original extracted files:

* ``RespuestaEnvioDTE_v10.xsd``: ``89d1102d2d78133af077651c574a4386299d2a3c3b406f8fe9f16884f27f4f68``
* ``EnvioRecibos_v10.xsd``: ``876865a1eb60def5c0109f9568ef4b6dca9301143efcbc727ab4ccc001f604e3``
* ``Recibos_v10.xsd``: ``20eaf6f76fa966b90bf2a7bd17f8b373a7fcb6c60f97aae11377cb2999c57605``
* ``SiiTypes_v10.xsd``: ``fb1e48eaff85b6f561348f363c6828cc62867b4dceb5d44c6f852a0c0f53b598``
* ``xmldsignature_v10.xsd``: ``427e3225cd379ae92bae464b892dbf964665af92d453ac61774cffab38b95edb``

SHA-256 of the files bundled here after line-ending and whitespace
normalization:

* ``RespuestaEnvioDTE_v10.xsd``: ``e0711d4dea4e86377de66f551081ed14105d9194ca8f5742f066f6631472a148``
* ``EnvioRecibos_v10.xsd``: ``f90e0835d4298e9dc3f36bafe2b2974739938deb19fea1ac870996b7e10a6ab5``
* ``Recibos_v10.xsd``: ``b6ef97da83c9698e0fdb1e8e25ee93a64a6a08b4f5bada5a6cb91153bcdf7ff6``
* ``SiiTypes_v10.xsd``: ``1b030524533204af149ffebd4ac54de9256bacd50b7cd277a3da9aee05007f1a``
* ``xmldsignature_v10.xsd``: ``9139b1bfae30dec716b88a7b58d7a0f87ec50c4dc3ee9e461f23b3ad07a660a9``
