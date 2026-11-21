1. Load a CAF for DTE type 46.
2. Give the supplier a valid Chilean RUT, a name, an address and a commune.
3. Create a vendor bill with document type 46. On each taxable line set the
   19% purchase VAT and exactly one VAT retention:
   - the total VAT withholding of the Chilean chart (`19% WH`, SII code 15); or
   - a partial (or product) VAT retention tax that you create yourself, because
     the Chilean chart ships none: a purchase tax, percentage, with the negative
     official rate and the SII code below. Exempt lines carry no tax.
4. Confirm the bill and press **Issue DTE**. The company issues and signs the
   DTE 46 and sends it to the SII as it does for customer invoices.

Supported retention codes and rates (SII "Formato Documentos Tributarios
Electrónicos" v2.5, 2026-02, section 4):

| SII code | Product            | Tax rate |
|----------|--------------------|----------|
| 15       | Total retention    | -19%     |
| 30       | Legumes            | -10%     |
| 31       | Wild produce       | -19%     |
| 32       | Cattle             | -8%      |
| 33       | Wood               | -8%      |
| 34       | Wheat              | -4%      |
| 36       | Rice               | -10%     |
| 37       | Hydrobiological    | -10%     |
| 38       | Scrap metal        | -19%     |
| 39       | PPA                | -19%     |
| 41       | Construction       | -19%     |
| 47       | Cardboard          | -19%     |
| 48       | Raspberries        | -14%     |

Each line states its code in `CodImpAdic`; the totals carry one `ImptoReten`
per code with its rate and retained amount, `IVANoRet` with the VAT that is not
retained (when greater than zero) and `MntTotal` = net + exempt + VAT − total
retained. Lines with different codes, including code 15, can be mixed.

Bills with another retention code (35, 40, 42 to 46, 49, the cambio de sujeto
codes 301, 321, ...), a retention whose rate differs from the official one, two
retentions on one line, a second category withholding, another currency or
negative lines are refused before a folio is reserved.
