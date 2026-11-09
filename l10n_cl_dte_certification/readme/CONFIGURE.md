The public receiver-activity catalogue is bundled in
`data/sii_economic_activities.json`. It contains 674 codes downloaded from
[the SII economic activity table](https://www.sii.cl/ayudas/ayudas_por_servicios/1956-codigos-1959.html)
on 2026-11-05, preserving six-digit codes, full descriptions, VAT status,
tax category and Internet availability. Metadata records the retrieval date,
source page SHA-256, catalogue version and canonical activities SHA-256.

Generation uses first-category, VAT-affected activities available online whose
complete descriptions fit the 40-character `GiroRecep` limit and ISO-8859-1.
Descriptions are never truncated. The catalogue is read locally: importing,
reviewing or generating test data makes no HTTP requests.

To refresh the public data explicitly, run the bundled maintenance script:

```console
python scripts/update_sii_activity_catalogue.py
```

Run it from the addon directory, inspect the data diff and rerun the module
tests before deploying an updated catalogue. Use `--source-html PATH` and
`--retrieved-on YYYY-MM-DD` to reproduce an update from a saved source page.
The updater is never invoked automatically by Odoo. Existing execution
snapshots retain the catalogue identity and selected values they froze.
