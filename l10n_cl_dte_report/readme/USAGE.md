1. Cree una muestra desde un DTE firmado y compatible.
2. Genere el PDF y revise el XML fuente, el TED/PDF417 y los datos impresos.
3. Agrupe hasta 20 muestras en un paquete; el sistema conserva el archivo y los hashes de sus fuentes.
4. Cargue la muestra por el canal indicado por el SII y registre su resultado en el expediente de certificación. La descarga local no cambia el estado de una etapa.

Se soportan facturas electrónicas 33/34 y boletas 39/41. Las notas 61/56 y los
demás tipos continúan bloqueados. El módulo instala la dependencia de boletas
para reutilizar su validación XSD, TED y firma.

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
