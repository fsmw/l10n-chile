# ADR-0008 — Tolerancia XMLDSig para DTE del SII

- **Estado:** aceptado
- **Fecha:** 2026-10-30
- **Paso:** S2 del plan `docs/parity/plan-l10n_cl_dte_sii.md` (rev 6)
- **Módulo:** `l10n_cl_dte_sii/services/incoming_dte_xml.py`
- **Contexto:** el validador rechazaba los 12 DTE reales de `xml-reales/`, que el
  SII aceptó. Bloqueaba V05 (`l10n_cl_dte_delivery_guide`), V08
  (`l10n_cl_dte_incoming`) y V16 (`l10n_cl_dte_mipyme_recovery`).

## Problema

La causa no era la firma sino la **canonicalización**. El validador anterior
exigía `CanonicalizationMethod` inclusivo, `<Transforms>` no vacío y calculaba
el digest con C14N inclusivo. Los documentos reales no se comportan así:

- Declaran C14N inclusivo pero el `SignatureValue` sólo verifica con C14N
  **exclusivo** del `SignedInfo`.
- Cuatro recibidos firman el digest del `Documento` **sin** el namespace
  `xmlns="http://www.sii.cl/SiiDte"`.
- Dos recibidos no declaran `<Transforms>` y uno declara sólo
  `enveloped-signature`, sin `Transform` de C14N.

## Matriz medida (12/12 verifican)

| Dimensión | Valores aceptados | Documentos reales |
|---|---|---|
| `SignatureValue` | C14N exclusivo del `SignedInfo` | 12/12 |
| `DigestValue` (1.er intento) | C14N exclusivo con namespace | 8 (7 emitidos + OPTIMACORP 889) |
| `DigestValue` (2.º intento) | C14N exclusivo sin `xmlns="http://www.sii.cl/SiiDte"` | 4 (BICE 978, BICE 1100, CELER 1097, Flota 1067) |
| `<Transforms>` | vacío (C14N implícito) | BICE 978, BICE 1100 |
| | `[C14N]` | 8 |
| | `[enveloped-signature]` | CELER 1097 |
| | `[enveloped-signature, C14N]` | ninguno en el corpus (se acepta por simetría) |
| `DigestMethod` | `sha1` (y `sha256` ya soportado) | sha1 en 12/12 |

Evidencia por documento: variante de firma, variante de digest y `Transforms`
quedan registrados en `validation_evidence` (`digest_variant`,
`signature_variant`, `transforms`, `declared_canonicalization`).

## Por qué el exclusivo reproduce a `dte-18`

`dte-18` firma el `SignedInfo` **aislado**: lo serializa sin el namespace
`SiiDte` heredado del `DTE`. Al verificar ese mismo `SignedInfo` dentro del
árbol, la C14N inclusiva arrastra los namespaces en scope del documento
(incluso los no utilizados, como `xsi`), mientras que la exclusiva sólo emite
los prefijos visiblemente utilizados. Por eso la forma equivalente en contexto
es la exclusiva y no la declarada.

## Orden de intentos

1. `SignatureValue`: exclusivo → inclusivo.
2. `DigestValue`: exclusivo con namespace → exclusivo sin namespace →
   inclusivo.
3. `Transforms` se compara contra la lista blanca exacta antes de calcular el
   digest.

Cada intento es una función determinista del mismo subárbol: se aceptan varias
serializaciones del **mismo** contenido, no contenido distinto. La variante que
verifica queda registrada para auditoría.

## Riesgo del digest sin namespace y cómo queda acotado

El segundo intento tolera que el `Documento` se haya firmado sin la declaración
de namespace del SII. El riesgo teórico es que un atacante retire
`xmlns="http://www.sii.cl/SiiDte"` para reutilizar un digest calculado sin
namespace. Queda acotado por tres controles, todos con prueba negativa:

1. **XSD antes de la firma.** `validate_dte_element` valida contra
   `data/xsd/DTE_v10.xsd` (o el XSD de boletas) **antes** de verificar la firma.
   Un documento con el namespace retirado cambia el namespace de todas sus
   etiquetas y es rechazado por el esquema
   (`test_stripped_namespace_document_is_rejected_by_the_schema`).
2. **Alteración de contenido.** Un byte distinto en el `Documento` falla en las
   tres variantes de digest (`test_one_changed_byte_in_document_fails_every_digest_variant`).
3. **Alteración de `SignedInfo`.** Cualquier cambio en el `SignedInfo` invalida
   el `SignatureValue` (`test_changed_signed_info_fails_signature_verification`).

El segundo intento sólo se evalúa cuando el primero falla y sólo elimina la
declaración del apex: nunca reescribe el contenido del documento.

## Deuda registrada (no resuelta en S2)

El firmante de este addon (`services/xml_signature.py`) firma el `SignedInfo`
en C14N **inclusivo** con `xmlns:xsi` en scope y el digest del `Documento` en
inclusivo. Por eso el validador conserva el inclusivo como último intento: sin
él, `account_move` rechazaría los DTE que el propio addon firmó
(`validate_dte_element(signed_dte)` antes de enviar).

Alinear la emisión a C14N exclusivo es un cambio de salida XML y no pertenece a
S2. Queda registrado como brecha **G23** en el plan; hasta entonces el inclusivo
se mantiene como fallback documentado.

## Qué sigue rechazándose

- `CanonicalizationMethod` distinto de C14N inclusivo o exclusivo C14N 1.0.
- `SignatureMethod` distinto de RSA-SHA1 / RSA-SHA256.
- `DigestMethod` distinto de SHA-1 / SHA-256.
- Cualquier combinación de `<Transforms>` fuera de la lista blanca.
- Más de una firma, referencias a IDs inexistentes o ambiguos, referencia a un
  elemento contenedor y firma que no cubre el `DTE` ni su documento fiscal.
- Firma que no verifica en ninguna de las dos variantes.
- Certificado fuera de vigencia en el `TmstFirma`, cadena no confiable o CRL
  que no cubre el instante firmado (`require_trust`).
- Documento que no cumple el XSD local del SII.

## Consecuencias

- Positivas: los 12 documentos reales verifican; la recepción deja de rechazar
  documentos aceptados por el SII; la variante usada es auditable.
- Negativas: se aceptan cuatro serializaciones de digest y dos de `SignedInfo`;
  el costo es superficie de tolerancia, no integridad, porque todas se calculan
  sobre el mismo árbol ya validado por XSD.
- Regresión obligatoria: `l10n_cl_dte_incoming`, `l10n_cl_dte_mipyme_recovery` y
  `l10n_cl_dte_delivery_guide`.
