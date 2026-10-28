# Esquema observado del RCV

Captura de solo lectura realizada el 2026-09-23 para el período `2026-08` con la POC en
`tools/sii-certification-lab/tmp/poc-rcv-ttao.py`. El archivo de fixture contiene solo
nombres de campo y tipos JSON; no incluye RUT, folios, nombres de proveedores ni
importes.

## Compra y venta

Las consultas `getResumen` respondieron `respEstado.codRespuesta = 0` y `data` como
arreglo. La respuesta también contiene `dataCabecera`, `metaData`, `pDB`, `totDocRes`,
`verF29` y `tieneEmisorAgresivo`.

Filas del resumen observadas:

`dcvCodigo`, `dcvNombreTipoDoc`, `dcvOperacion`, `dcvTipoIngresoDoc`, `rsmnCodigo`,
`rsmnEstadoContab`, `rsmnFechModificacion`, `rsmnIVAUsoComun`, `rsmnLink`, `rsmnMntExe`,
`rsmnMntIVA`, `rsmnMntIVANoRec`, `rsmnMntNeto`, `rsmnMntTotal`, `rsmnTipoDocInteger`,
`rsmnTotDoc`, `rsmnTotalRutEmisor`.

Los campos fiscales de montos y conteo fueron números JSON; texto de tipo/ingreso fue
string; varios campos opcionales llegaron `null`. Normalizar nulos explícitamente. El
resumen indicó Compras Registro tipos 33/34 y Ventas Registro 110/33/34/61.

Las consultas `getDetalleCompra` y `getDetalleVenta` respondieron filas como arreglos
con, entre otros, `detTipoDoc`, `detNroDoc`, `detFchDoc`, `detRutDoc`, `detDvDoc`,
`detRznSoc`, `detMntExe`, `detMntNeto`, `detMntIVA`, `detMntIVANoRec`, `detIVAUsoComun`,
`detMntTotal`, `detEventoReceptor`, `detFecAcuse`, `detFecRecepcion`, `detFecReclamado`,
`detTipoDocRef`, `detFolioDocRef`, `detAnulado`, `detTipoTransaccion` y
`descTipoTransaccion`. Hay campos específicos de exportación, liquidación, retenciones e
impuestos adicionales; se preservarán solo si se define su semántica. Las variantes de
documento tienen campos opcionales nulos o numéricos, por lo que el adaptador no debe
convertir `null` a cero sin regla.

Detalle observado: Compras Registro 13 tipo 33 + 4 tipo 34; Ventas Registro 4 tipo 110 +
2 tipo 33 + 24 tipo 34 + 7 tipo 61. Para Compras Pendiente el resumen enumeró tipos 33,
34 y 61, pero sus detalles llegaron vacíos; no es un conjunto “sin movimiento”. Compras
Reclamado no listó tipos en agosto.

La consulta desde Odoo con el certificado digital de la compañía devolvió 17 documentos
en Compras y 37 en Ventas. Compras quedó `partial` porque el detalle
COMPRA/PENDIENTE/tipo 33 respondió código funcional 99; la implementación permite ese
código solo en ese contexto, conserva el resumen y fuerza `partial`. Código 99 en otras
operaciones/estados/tipos sigue siendo error. Ventas quedó `success`. No se guardó el
certificado en la captura.

## Boletas

La POC directa no implementa ni captura una consulta SII de boletas
diarias/individuales. El repositorio externo `baseapi-sdk` documenta métodos de producto
BaseAPI; eso no demuestra que sean endpoints directos ni contrato público del SII. La
primera versión solo mostrará datos de boletas si se identifica y valida una llamada
directa de solo lectura; en otro caso dejará la sección como no disponible/pendiente,
sin fabricar detalle.

Como prueba adicional se consultaron los tipos 35, 38, 39, 41 y 48 con `getDetalleVenta`
para agosto; el SII respondió correctamente con cero filas para todos. Eso no valida ni
reemplaza una consulta de resumen diario y no permite concluir que las boletas del
período sean cero.

## Implicancias

- `rsmnTotDoc` es cantidad agregada del resumen; nunca crear una factura por esa fila.
- Comparar cantidad e importes del detalle con el resumen; la falta de detalle mantiene
  el estado `partial`, incluso ante el código funcional 99 observado en Compras
  Pendiente.
- El RUT del emisor consultado identifica al contribuyente seleccionado; cada documento
  trae la contraparte en `detRutDoc` y `detDvDoc`. El significado de `detRutDoc` se
  interpreta con la dirección Compra/Venta.
- Evaluar `respEstado.codRespuesta` además del HTTP 200.
- Esta captura de agosto es evidencia de estructura/versiones observadas, no un esquema
  oficial estable.
