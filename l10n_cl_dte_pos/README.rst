==================================
Chile - POS DTE Bridge
==================================

Integra pedidos de Punto de Venta de Odoo 18 con la emisión de DTE chilena.
El addon se instala junto con ``point_of_sale`` y ``l10n_cl_dte_sii``; la guía
base no depende de este módulo.

Uso
===

Para compañías chilenas, el cajero elige **Boleta** o **Factura** en la
pantalla de pago. Los pedidos sincronizados generan su ``account.move`` con el
tipo fiscal correspondiente: 39/41 para boleta, 33/34 para factura y 61 para
devolución POS con referencia al DTE original. Los documentos exentos usan el
tipo exento correspondiente. El contacto “Consumidor Final Anónimo” se asigna
por defecto a boletas; para factura se exige cliente identificado con datos
tributarios completos. Esta validación se repite en el servidor al sincronizar
la orden, para impedir que una sincronización directa u offline omita las
reglas de la pantalla.

El pedido mantiene su identidad UUID de Odoo para que una reconexión o reintento
no genere otra factura. Si el terminal opera sin conexión, el comprobante
impreso antes de sincronizar se identifica como **DTE pendiente de
sincronización** y no muestra folio ni timbre. Al sincronizar, Odoo crea y
contabiliza el documento en el servidor, genera su XML/TED y devuelve folio,
estado y timbre al POS.

El envío al SII permanece explícito: en el formulario del pedido POS un usuario
DTE puede enviar el documento y consultar el estado. La integración delega esas
operaciones al ciclo protegido de ``account.move``; un resultado incierto no se
reenvía ciegamente. La configuración requiere diario electrónico de venta,
punto de venta SII en línea, sucursal y oficina. El módulo no realiza llamadas
reales al SII durante instalación ni pruebas.

Si un envío de factura queda incierto y no tiene TrackID, un responsable DTE
puede iniciar la conciliación desde el pedido POS. La decisión, su evidencia y
la auditoría se gestionan en el flujo del core sobre ``account.move``; el POS no
crea un ciclo fiscal paralelo ni habilita el reenvío automático.

Sólo los medios de pago marcados como **Pago con tarjeta** solicitan el número
de voucher y lo conservan en el pedido POS para trazabilidad. Configure esta
opción en el formulario del medio de pago; el tipo de diario no basta para
distinguir tarjetas de transferencias. Al actualizar, la migración marca los
medios bancarios existentes para preservar el comportamiento anterior; revise
esa selección y desactívela para transferencias u otros medios sin voucher. El recibo Chile muestra los
datos tributarios del emisor, folio/tipo y TED cuando están disponibles. El
archivo PDF de factura no se genera ni envía automáticamente desde POS; se
imprime el recibo de POS y las acciones de correo se ejecutan desde los flujos
explícitos de Odoo.

Seguridad y límites
===================

El grupo de usuario POS no hereda el grupo contable de operación DTE. El POS
conserva el acceso de lectura de Odoo limitado a facturas vinculadas a pedidos
POS; los botones de envío y consulta delegan en el ciclo protegido de
``account.move``, que valida el rol POS, la compañía y el vínculo al pedido.
La conciliación de envíos inciertos sigue reservada al responsable DTE. El POS
sólo recibe por ``pos.order`` una proyección de tipo, folio, estado, TrackID y
TED del ``account.move`` vinculado; el dataset POS no carga facturas. El XML y
la clave del CAF no se exponen al cliente POS.

No se agrega menú independiente: la entrada es el POS existente y la salida
administrativa está en el pedido POS vinculado al documento contable. No se
requieren registros ``data/`` propios. Los assets ``static/`` extienden las
pantallas de pago, ticket, selección de cliente y recibo. El addon depende de
``pdf417gen`` para dibujar el timbre PDF417.

Validación
==========

Las pruebas Odoo cubren los tipos 33/34/39/41/61, datos inválidos, render del
TED, permisos e instalaciones ajenas a Chile. Un tour de navegador valida
selección Boleta/Factura, pago y sincronización de la venta, devolución total y
nota 61 referenciada al DTE 39. Reintentar la generación reutiliza el mismo
``account.move``. El tipo DTE y voucher quedan inmutables después de vincular
``account.move``. El tour usa CAF/certificado sintéticos en ``SIIDEMO`` y no
realiza llamadas al SII. La prueba vuelve a sincronizar un UUID cuyo pedido ya
fue pagado: obtiene el mismo ``pos.order`` y ``account.move`` y descarta valores
fiscales divergentes enviados en el replay. La sincronización desconectada se
apoya en el UUID de ``pos.order`` del core POS. Todavía requiere prueba funcional
con red interrumpida/restaurada con red real y UAT. El tour automatizado simula
el corte y valida la recuperación del folio; ``SIIDEMO`` no acredita aceptación
del SII.

Actualización de bases POS heredadas
====================================

El addon de origen se llamaba ``l10n_cl_edi_pos``. La instalación nueva usa
``l10n_cl_dte_pos``; el shim con el nombre anterior depende de este addon y debe
quedar en ``addons_path`` para bases migradas. Durante una actualización, ubique
el fork antes del checkout ``dte-18`` y actualice ambos módulos
(``l10n_cl_dte_pos`` y ``l10n_cl_edi_pos``) en la misma ejecución. El shim
desactiva y conserva la vista antigua que duplicaba el voucher. Los campos
``pos.order.invoice_type`` y ``voucher_number`` conservan sus columnas y valores;
no se reescriben pedidos, facturas ni DTE. Mantenga el shim instalado en bases
actualizadas. En instalaciones limpias instale sólo ``l10n_cl_dte_pos``. No
copie el ``journal_id`` antiguo de tipo general a ``invoice_journal_id``:
configure explícitamente el diario de venta DTE y el punto de venta
SII/sucursal/oficina. Si esa configuración falta, la caja chilena no abrirá
hasta que un administrador la complete.

Antes de actualizar una base operativa, ensaye sobre una copia restaurable de
base y filestore y reconcilie UUID/órdenes, facturas vinculadas, folios, estados,
XML y hashes de adjuntos. La prueba sintética del post-migration no sustituye
esa reconciliación ni la UAT de caja.
