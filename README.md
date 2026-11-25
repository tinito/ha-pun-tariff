# PUN Tariff

Home Assistant integration that computes what the electricity from the grid costs under an
Italian contract indexed to the **PUN Index GME**, from your energy meter and a contract you
describe in YAML: spread, losses, dispatching, network and system charges, excise, fixed fees, VAT.

- **Any PUN-indexed formula.** The contract is a YAML document with templates, so spreads,
  losses, discounts and fees of any offer can be written down. Monthly PUN by band (F1/F2/F3),
  monthly flat, hourly or quarter-hourly.
- **Matches the invoice.** On a monthly-by-band contract the month total converges to the
  invoice: the July–October 2026 invoices of the author's contract are reproduced within €0.03.
- **Official prices, with or without credentials.** The PUN comes from the GME website download,
  which needs no registration, or from the official GME API (credentials from GME, free for
  personal use). With the API, the website is the automatic fallback. History is read from GME on
  demand.
- **Energy dashboard.** *Cost this month* works as the grid cost entity (`stat_cost`).
- **Check your contract.** The `pun_tariff.calculate_bill` action prices a past month from the kWh
  of an invoice and returns the difference.

## Entities

| Entity | Unit | Notes |
|---|---|---|
| Price now | EUR/kWh | all per-kWh components at the current PUN, VAT included |
| Cost this month | EUR | `total`, resets every month; monthly fees counted by the share of the month elapsed |
| Energy this month | kWh | with the split by band in the attributes |
| Cost last month | EUR | final bill of the previous month, with the breakdown |
| PUN now | EUR/kWh | price of the current quarter hour (or hour) |
| PUN F1 / F2 / F3 this month, PUN this month | EUR/kWh | monthly averages so far |
| Band | F1/F2/F3 | ARERA band now (national holidays included) |

The breakdown of every bill (each `per_kwh` and `per_month` component, taxable, VAT) is in the
attributes of the cost sensors.

## Installation

1. HACS / Marketplace → custom repository `tinito/ha-pun-tariff` (Integration), install, restart.
2. Settings → Devices & services → Add integration → **PUN Tariff**.
3. Choose the price source:
   - **GME website download** (default): no credentials. It uses the download behind the GME
     results page, the same data as the API, but not a documented interface: it may change.
   - **Official GME API**: register at
     [api.mercatoelettrico.org](https://api.mercatoelettrico.org/users/RegistrationForm/RegistrationRequest).
     GME asks for the acceptance of the terms of use and a signed declaration sent by PEC, then
     emails user and password. Free for personal, informational use; cite the source as
     "Gestore dei Mercati Energetici S.p.A." (the entities carry this attribution). When the API
     does not answer, the prices come from the website; when it rejects the credentials, Home
     Assistant also asks for new ones.

   Start with the website and switch to the API when the credentials arrive: Settings → Devices &
   services → PUN Tariff → ⋮ → **Reconfigure**. The `source` attribute of *PUN now* shows which
   source answered last.
4. Pick the energy sensor of the grid meter (kWh, increasing) and write the contract: see the
   **[contract guide](docs/contract-guide.md)**, which also has a brief for having an AI assistant
   write the contract from your invoices. Examples are in [examples](examples/).
5. Energy dashboard → grid consumption → *Use an entity tracking the total costs* → **Cost this
   month**.

## How it works

- **PUN:** from the website or the API, with the same values: the website download returns hours
  and quarter hours together, the API one granularity per request. Downloaded once a day from the start of the month to tomorrow (day-ahead prices are
  published around 13:00), with an extra attempt in the afternoon until tomorrow's prices arrive.
  The monthly price of a band is the arithmetic mean of the hourly PUN of its hours, which
  reproduces the official monthly values to the fifth decimal.
- **Bands:** F1 Monday–Friday 8–19; F2 Monday–Friday 7–8 and 19–23, Saturday 7–23; F3 the rest,
  Sundays and national holidays (including 4 October from 2026).
- **Energy:** every reading of the energy sensor is split over the hours or quarter hours it
  covers and by band. A counter that goes back (reset or glitch) is ignored. Interval contracts
  price each interval as soon as its PUN is known; kWh of intervals without a price are kept and
  priced later.
- **Month end:** the closed month's bill is stored as *Cost last month* and the counters restart.

## Limits

- Values that the supplier or ARERA change over time (dispatching, network and system charges)
  are part of the contract: update them in the options when an invoice shows new values.
- Monthly-by-band contracts: the month total changes while the averages settle, so the Energy
  dashboard can show small negative hourly costs.
- The meter in Home Assistant is not the distributor's meter: expect about 1% of difference in kWh.

## Development

Pure modules (bands, GME parsing, contract, month accounting) have tests that run without Home
Assistant:

```bash
pip install pytest jinja2 pyyaml
pytest
```

`PUN_TARIFF_LIVE=1 pytest tests/test_live.py` (with `aiohttp`, in an environment without
`pytest-homeassistant-custom-component`, which blocks the network) downloads October 2026 from the GME
website and checks the official monthly PUN by band.

The integration tests in `tests_ha` run Home Assistant with the price download mocked:

```bash
pip install pytest-homeassistant-custom-component
pytest tests_ha
```

Data source: Gestore dei Mercati Energetici S.p.A. — PUN Index GME. Not affiliated with GME or ARERA.
