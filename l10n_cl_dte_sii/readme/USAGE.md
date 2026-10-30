Configure el ambiente DTE, resolución, giro, actividad económica y certificado
en la compañía. Habilite un diario electrónico con sucursal y oficina, e importe
y active un CAF vigente para cada tipo de documento.

Al contabilizar una factura electrónica, el módulo reserva el siguiente folio
autorizado, genera el DTE y el sobre firmados y adjunta ambos al documento. En
**Facturación > Clientes > DTE de salida**, un usuario DTE puede enviar el sobre
explícitamente y consultar su estado. ``SIIDEMO`` no llama al SII.

Cuando el SII informa aceptación o aceptación con reparos, el usuario DTE puede
usar **Enviar DTE al receptor** en la factura. La acción crea un sobre firmado
con el RUT del destinatario y lo agrega a la cola de correo de Odoo; no reenvía
el sobre SII ni se ejecuta automáticamente al consultar el estado. La pestaña
DTE SII muestra destinatario, actor, hora y estado de cada intento. Un intento
con error permite un nuevo envío iniciado por el usuario; una cola activa o un
envío completado bloquean duplicados.

Las boletas y facturas exentas usan su tipo de documento correspondiente. Las
notas de crédito y débito requieren referencias tributarias antes de emitir.
Revise los datos del emisor, receptor y documento antes de contabilizar.

Si una factura 33/34 queda en error luego de un despacho sin TrackID, no la
reenvíe directamente. Un responsable DTE puede usar **Conciliar envío incierto**
desde la factura y registrar la fuente y evidencia consultadas. Si confirma la
recepción, debe informar el TrackID de diez dígitos; el documento queda
disponible para consultar su estado. Si confirma que el SII no la recibió, la
conciliación habilita un reenvío que el usuario debe iniciar explícitamente. La
decisión, evidencia, responsable y hora quedan en un historial inmutable. Este
flujo no realiza llamadas al SII y no está disponible para boletas.

Al instalar este módulo en una base que conserve las columnas del emisor
legado, la migración copia XML, sobre, TrackID y estado de entrega al receptor
a sus campos actuales. Sólo llena destinos vacíos y mantiene los valores fuente
para reconciliación; no genera XML, consume folios ni llama al SII. La migración
debe validarse con una copia consistente de base y filestore antes de actualizar
una base histórica.

Al instalar este módulo en una base que conserve las columnas del emisor
legado, la migración copia XML, sobre, TrackID y estado de entrega al receptor
a sus campos actuales. Sólo llena destinos vacíos y mantiene los valores fuente
para reconciliación; no genera XML, consume folios ni llama al SII. La migración
debe validarse con una copia consistente de base y filestore antes de actualizar
una base histórica.
