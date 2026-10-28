# Chile - Registro de Compras y Ventas SII

Este addon consulta el RCV en modo lectura para compañías chilenas, conserva resúmenes y
documentos con sus sincronizaciones, y permite revisarlos junto a `account.move` desde
Contabilidad.

## Estado del adaptador

- Compras y Ventas: flujo de sesión SII con el certificado digital válido de la
  compañía. El adaptador revisa `respEstado.codRespuesta` además del HTTP.
- Compras Pendientes: el SII puede devolver tipos y cantidades en el resumen sin
  entregar filas de detalle. La sección queda parcial; no se interpreta como ausencia de
  documentos.
- Ventas Boletas: el primer corte deja la sección pendiente hasta validar una llamada
  directa de solo lectura al SII. No usa BaseAPI ni inventa comprobantes individuales a
  partir de agregados.
- CSV: formato de respaldo documentado en el asistente; la ejecución queda identificada
  como CSV y no como sincronización en vivo.

## Credenciales

Usa el certificado que `res.company._get_digital_signature` selecciona en `l10n_cl_edi`.
El certificado y la llave PEM se escriben temporalmente con permisos restringidos para
la sesión TLS y se eliminan al finalizar. No se guardan cookies, tokens, contraseñas ni
llaves en las tablas RCV.

## Comparación y borradores

Los documentos se identifican por compañía, operación, tipo DTE, RUT emisor y folio. Los
candidatos múltiples requieren resolución manual. La acción de borrador exige una
revisión explícita; requiere XML DTE válido o líneas, cuenta contable e impuestos
elegidos por el usuario. El resultado es siempre un borrador que debe revisarse en Odoo.

### Exportaciones con receptor genérico

Las ventas DTE 110 cuyo receptor en RCV es `55555555-5` se buscan por compañía, RUT
emisor, tipo y folio, sin exigir que el identificador extranjero del cliente coincida
con ese RUT genérico. La regla funciona con facturas emitidas en Odoo y no requiere
recuperación MiPyme. Compras y otros tipos conservan sus controles.

La conciliación compara bases, exentos e impuestos con los saldos contabilizados en
moneda de compañía, sin usar el tipo de cambio actual ni modificar la factura. Los
borradores y los impuestos sin evidencia suficiente quedan en revisión; duplicados y
diferencias reales mantienen su estado correspondiente.

Para revisar documentos previamente marcados «Falta en Odoo», seleccione los RCV
afectados y ejecute **Conciliar** después de actualizar el módulo. Se recalculan el
vínculo y el estado incluso si ya había una factura enlazada. Repetir la acción es
idempotente: no crea facturas, no cambia RUT ni reenvía DTE. Una factura que no existe
seguirá como faltante; un enlace a otra factura requiere revisión.

### Borradores automáticos de compras

La lectura diaria está desactivada por defecto. Antes de activarla, configure en la
compañía una persona de servicio activa con acceso a la compañía y firma digital SII
válida. Verifique primero en QA que esa firma pueda leer el detalle completo del estado
**Registro** para el mes actual y el anterior. Luego active **Automatización RCV** en la
ficha de compañía. El cron reutiliza la cola normal de sincronización, crea borradores
para documentos de compras Registro cuyas secciones de detalle estén completas e incluye
documentos SII ya almacenados de esos dos períodos; nunca contabiliza las facturas.

Revise **Error de automatización** en cada documento RCV con estado Revisión manual. El
mensaje se conserva hasta que un reproceso tenga éxito o el documento se resuelva. Los
candidatos ambiguos no generan una factura automática.

Si el XML llega por correo después de crear el borrador desde RCV, queda adjunto a la
misma factura. Si aún está en borrador, sus líneas resumidas se reemplazan por el
detalle del XML; las facturas en otro estado solo reciben el adjunto. Para rollback,
desactive primero la opción por compañía; los borradores, enlaces y archivos existentes
se conservan para revisión contable.

### Conciliación fiscal y clasificación del IVA

Para comparar el documento, el IVA fiscal RCV se calcula como la suma del IVA
recuperable, no recuperable y de uso común. Por eso, un IVA recuperable de cero no exige
un XML cuando el desglose completo explica el total. El sistema compara el neto, exento,
IVA fiscal, otros impuestos identificados y total con el documento único de Odoo.

La conciliación confirma la identidad e importes del documento; no aprueba la
clasificación del crédito fiscal ni valida una declaración F29. Si los importes fiscales
coinciden pero Odoo y el RCV asignan el IVA a categorías distintas, el documento queda
conciliado y muestra una observación tributaria con ambas asignaciones. El sistema solo
alinea impuestos en borradores de integración cuando existe procedencia comprobable y
una única configuración compatible.

Use **Conciliar** en un documento o seleccione varios y ejecute **Conciliar
seleccionados** para reevaluar estados ya existentes. La misma evaluación se usa al
sincronizar el RCV y recibir un XML. El resultado por lote muestra los documentos
conciliados, las observaciones de clasificación y los casos que aún requieren atención.
Los errores de un registro no detienen al resto. Un XML ausente, inválido, no
verificable o con identidad distinta se informa con su causa cuando esa evidencia sea
necesaria para resolver una contradicción fiscal real.

## Importar CSV

CSV UTF-8 con encabezados requeridos
`tipo_documento,rut_contraparte,folio,fecha,neto,exento,iva,total`. Se aceptan además
`razon_social,iva_no_recuperable,iva_uso_comun`. Los montos son enteros en pesos. El
archivo y su SHA-256 quedan asociados a la ejecución.

## Prueba de esquema

La respuesta observada y saneada está documentada en `docs/rcv-response-schema.md`. El
SII no publica un contrato estable para esta fachada; validar el payload en cada cambio
de transporte.
