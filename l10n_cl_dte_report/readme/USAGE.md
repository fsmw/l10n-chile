1. Cree una muestra desde un DTE firmado y compatible.
2. Genere el PDF y revise el XML fuente, el TED/PDF417 y los datos impresos.
3. Agrupe hasta 20 muestras en un paquete; el sistema conserva el archivo y los hashes de sus fuentes.
4. Cargue la muestra por el canal indicado por el SII y registre su resultado en el expediente de certificación. La descarga local no cambia el estado de una etapa.

Se soportan facturas electrónicas 33/34, boletas 39/41 y notas de crédito 61 y
débito 56. Los demás tipos continúan bloqueados. El módulo instala las
dependencias de boletas y notas para reutilizar sus validaciones de esquema,
TED y firma.

Las notas se imprimen en A4 desde su XML firmado, con las referencias originales
(tipo, folio, fecha, código y motivo), detalles, impuestos y total. El código de
referencia distingue anulación y corrección de montos; el motivo conserva la
descripción del ajuste o devolución parcial. La impresión no recalcula el
ajuste desde la contabilidad ni cambia el documento fiscal o sus evidencias.
La acción térmica de 80 mm continúa limitada a boletas 39/41.

Para una boleta, **Thermal PDF (80 mm)** genera una representación adicional
desde el XML firmado. Funciona tanto para documentos originados en factura como
en POS. Conserva la muestra A4, su hash y el XML original; el PDF térmico se
genera al solicitarlo y no se guarda como evidencia A4. Esta acción requiere
el grupo DTE Manager.

El papel térmico usa páginas de 80 x 297 mm y permite paginación. La impresión
física, los controladores y el corte del rollo se configuran fuera del módulo.
El timbre PDF417 se conserva como información opcional en la boleta impresa:
la [Resolución SII 207/2025](https://www.sii.cl/normativa_legislacion/resoluciones/2025/reso207.pdf)
eliminó su obligatoriedad desde el 1 de enero de 2026; el TED permanece en el XML.
