# Contract guide

PUN Tariff computes the bill from a **contract written in YAML**. This guide explains the format,
how to write it from your invoices, and how to check it. Section 4 is a ready-made brief for an
AI assistant that reads your invoices and writes the contract for you.

## 1. Format

```yaml
name: My supplier, My offer          # shown as the integration title
pun: monthly_bands                   # how the PUN is applied, see below
vars:                                # optional numbers used in the templates
  spread: 0.018
  losses: 0.10
per_kwh:                             # EUR/kWh before VAT, one entry per invoice line
  energy: "{{ (pun + spread) * (1 + losses) + 0.01773 - 0.0025 }}"
  network: 0.047922                  # network and system charges per kWh
  excise: 0.0227
per_month:                           # EUR/month before VAT
  supplier: 5.76
  network_fixed: 1.92
  power: "{{ 1.975556 * 4.5 }}"      # power fee x contracted kW
  other: "{{ 2.50 + 1.50 - 0.25 }}"
vat: 0.10                            # fraction
```

Every value in `per_kwh` and `per_month` is a number or a **Home Assistant template** that must
render a number. The bill is:

    total = (sum of per_kwh x kWh + sum of per_month x share of the month) x (1 + vat)

The TV licence fee (canone RAI) is not part of the electricity bill: leave it out.

### `pun`: how the index is applied

| Value | The PUN in the templates is | Use it when the offer says |
|---|---|---|
| `monthly_bands` | the monthly average of the band (F1, F2, F3) of each kWh | "PUN medio mensile per fascia" |
| `monthly_flat` | the monthly average of all hours | "PUN medio mensile" / monorario |
| `hourly` | the PUN of the hour of each kWh | "PUN orario" |
| `quarter_hourly` | the PUN of the quarter hour of each kWh | "PUN Index GME quartorario", "su base quartoraria" |

Monthly modes price the month's kWh by band with the month's averages, so the month total moves
until the month is over and then matches the invoice. Interval modes price each hour or quarter
hour when it ends.

### Template variables

| Variable | Meaning |
|---|---|
| `pun` | PUN applied to the kWh being priced, EUR/kWh (see above) |
| `band` | `"F1"`, `"F2"` or `"F3"` |
| `pun_f1`, `pun_f2`, `pun_f3`, `pun_mono` | monthly averages so far, EUR/kWh |
| `kwh_month` | kWh of the month so far |
| `year`, `month`, `days_in_month` | the month being billed |
| any name in `vars` | your numbers |

In interval modes the `per_kwh` values are evaluated once per interval.

## 2. Writing it from your invoices

Italian invoices have a summary ("Scontrino dell'energia" or "Quadro di sintesi") and a detail
section ("Elementi di dettaglio"). You need:

1. **Supplier energy price** ("spesa per la vendita, quota consumi"): the offer's formula is in the
   "Box dell'offerta" or in the contract (CTE). Typical forms:
   - `PUN + spread`, `PUN x (1 + λ) + spread`, `(PUN + spread) x (1 + λ)`; λ (losses) is 10% for
     low voltage. Check which terms the losses multiply: try the variants on one month and keep the
     one that gives the invoiced amount.
   - dispatching: a supplier value (PD) or the ARERA CDISPD. Its value can change month by month:
     put it in `vars` and update it from each invoice.
   - discounts per kWh (negative numbers).
2. **Network and system charges per kWh** ("spesa per la rete e oneri generali, quota energia"):
   the invoiced amount divided by the kWh. It changes every quarter (ARERA updates).
3. **Monthly amounts**: supplier fixed fee, network fixed fee, power fee (EUR/kW/month x kW),
   any other line (green energy option, services, bonuses as negative numbers). Annual fees: divide
   by 12.
4. **Excise** (accisa): EUR/kWh from the tax detail. Residential supplies up to 3 kW have an
   exemption on the first kWh: write it with `kwh_month` if it applies.
5. **VAT**: 10% for households.

## 3. Checking it

Use the `pun_tariff.calculate_bill` action (Developer tools → Actions) for each invoice:

```yaml
action: pun_tariff.calculate_bill
data:
  month: "2026-10"
  kwh_f1: 109.24
  kwh_f2: 92.72
  kwh_f3: 88.51
  expected_total: 138.36     # invoice total without the TV fee
```

The response has every component, the PUN used and the difference from the invoice. A correct
contract stays within a few cents; the rest is the supplier's rounding of each line. Interval
contracts (`hourly`, `quarter_hourly`) are checked with the band averages, so expect a larger gap
there.

## 4. Brief for an AI assistant

Copy this text into an AI assistant together with the PDF of the contract (CTE / scheda sintetica)
and two to four recent invoices. Remove personal data you do not want to share (name, address,
POD, tax code, IBAN): they are not needed.

> You are writing an electricity contract for the Home Assistant integration "PUN Tariff". Read the
> attached contract conditions and invoices and produce one YAML document in exactly this format:
>
> ```yaml
> name: <supplier> <offer name>
> pun: monthly_bands | monthly_flat | hourly | quarter_hourly
> vars: {<name>: <number>, ...}
> per_kwh: {<name>: <number or template>, ...}     # EUR/kWh before VAT
> per_month: {<name>: <number or template>, ...}   # EUR/month before VAT
> vat: <fraction>
> ```
>
> Rules:
> 1. Templates use Jinja syntax, e.g. `"{{ (pun + spread) * (1 + losses) }}"`, and may use only:
>    `pun`, `band`, `pun_f1`, `pun_f2`, `pun_f3`, `pun_mono`, `kwh_month`, `year`, `month`,
>    `days_in_month` and the names in `vars`. All prices in EUR/kWh, not EUR/MWh.
> 2. Choose `pun` from the contract text: monthly average by band → `monthly_bands`; monthly
>    average → `monthly_flat`; hourly → `hourly`; quarter-hourly ("quartorario", "su base
>    quartoraria", with a meter that records at least hourly) → `quarter_hourly`.
> 3. `per_kwh` must contain, as separate entries: the supplier energy price (index, spread,
>    losses, dispatching, discounts), the network and system charges per kWh, the excise.
>    `per_month` must contain every fixed line: supplier fixed fee, network fixed fee, power fee
>    (EUR/kW/month × contracted kW), other recurring lines and bonuses (negative). Annual amounts
>    are divided by 12. Leave out the TV licence fee and one-off charges.
> 4. Put values that change over time (spread, dispatching PD or CDISPD, network charges) in
>    `vars`, with the value of the most recent invoice, and say which invoice line they come from.
> 5. Verify the YAML against every invoice: for each one compute
>    `sum(per_kwh × kWh of each band) + sum(per_month)`, times `1 + vat`, using the monthly PUN by
>    band printed on that invoice and that invoice's values of the `vars`. Report a table with
>    month, computed total, invoice total without TV fee, difference. If a difference is more than
>    €0.05, find the cause (losses applied to the wrong terms, a missing line, a value that changed
>    in that month) and fix the YAML before answering.
> 6. Answer with: the YAML, the verification table, the list of values to update over time and
>    where to find them on the invoice, and any inconsistency found in the documents.

Then paste the YAML in the integration options and confirm it with `calculate_bill`.

## 5. Examples

- [ELIT StartMyHome](../examples/elit-startmyhome.yaml): monthly PUN by band, spread, losses on PUN
  and spread, supplier dispatching. Reproduces the July–October 2026 invoices within €0.03.
- [MET FlexiWeb ZERO Luce](../examples/met-flexiweb-zero.yaml): quarter-hourly PUN with losses,
  ARERA CDISPD, annual fixed fee.
