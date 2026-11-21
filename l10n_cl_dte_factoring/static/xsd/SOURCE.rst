SII Archivo Electrónico de Cesión (AEC) schema source
=====================================================

The seven XSD files in this directory were extracted from the SII's
``schema_cesion_201912.zip`` at
https://www.sii.cl/factura_electronica/schema_cesion_201912.zip, linked as
"Formato XML del Archivo Electrónico de Cesión" by
https://www.sii.cl/factura_electronica/form_ele.htm.

Retrieved 2026-11-21. Line endings were converted from CRLF to LF and
trailing whitespace was removed, as the repository's pre-commit hooks require.
The parsed schema content is unchanged. ``AEC_v10.xsd`` is the entry point; it
includes ``Cesion_v10.xsd`` and ``DTECedido_v10.xsd``, which include the
remaining files.

SHA-256 of the original ZIP:
``7e719c20e75474f41f00fcd26cb9521a422f78ed3747c88c3f31d5d18ac40706``.

SHA-256 of the original extracted files:

* ``AEC_v10.xsd``: ``809890d58376ef74bbd4162883df6e89d09231e783e0368486d497fce7308666``
* ``Cesion_v10.xsd``: ``ebd5bb1c7a6e6b349b559146f696e082301094021a921947766c5ca863e98e16``
* ``DTECedido_v10.xsd``: ``8cf524ec13ac2c48d99aed73ccd25c0a6a8c74dd81925f35b416c78ce22f0618``
* ``DTE_v10.xsd``: ``1ba39d9bdaf96955cf6f3c36a7ce004cc6df18c4dd759705af44dc56f94b2d28``
* ``Recibos_v10.xsd``: ``20eaf6f76fa966b90bf2a7bd17f8b373a7fcb6c60f97aae11377cb2999c57605``
* ``SiiTypes_v10.xsd``: ``5002fef1cd9592de1191359f1995592d48d18f2274c5659bf2e47129807f434d``
* ``xmldsignature_v10.xsd``: ``427e3225cd379ae92bae464b892dbf964665af92d453ac61774cffab38b95edb``

SHA-256 of the files bundled here after line-ending and whitespace
normalization:

* ``AEC_v10.xsd``: ``286217bfabf84b46e3d2d68431a204812d73544e87a764f3009aa7a2d1edbd33``
* ``Cesion_v10.xsd``: ``5a31b4445fca88899a0df75aa5129751d8d7dbcf61621d52757863ca385645f3``
* ``DTECedido_v10.xsd``: ``1693d05030e24cc6940babe24d19efabbd7be85fe144c535a5a6fe00a16f4162``
* ``DTE_v10.xsd``: ``1ba39d9bdaf96955cf6f3c36a7ce004cc6df18c4dd759705af44dc56f94b2d28``
* ``Recibos_v10.xsd``: ``b6ef97da83c9698e0fdb1e8e25ee93a64a6a08b4f5bada5a6cb91153bcdf7ff6``
* ``SiiTypes_v10.xsd``: ``aca9fbb251e0d9d317239cf836cc69b6e0cfa81fb048632df5c4901e09028df5``
* ``xmldsignature_v10.xsd``: ``9139b1bfae30dec716b88a7b58d7a0f87ec50c4dc3ee9e461f23b3ad07a660a9``
