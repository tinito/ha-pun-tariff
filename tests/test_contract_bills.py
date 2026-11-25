"""The ELIT 2026 invoices (July-October), reproduced from the values printed on them."""
import pytest
import yaml

from pun_tariff_pure.contract import ContractError, bill, parse_contract

ELIT = """
name: ELIT StartMyHome
pun: monthly_bands
vars: {spread: 0.018, losses: 0.10, pd: 0.01773, discount: -0.0025, network: 0.047922}
per_kwh:
  energy: "{{ (pun + spread) * (1 + losses) + pd + discount }}"
  network: "{{ network }}"
  excise: 0.0227
per_month:
  supplier: 5.76
  network_fixed: 1.92
  power: "{{ 1.975556 * 4.5 }}"
  green_energy: 2.50
  advice: 1.50
  email_bonus: -0.25
vat: 0.10
"""

# month: (PUN F1, F2, F3), kWh (F1, F2, F3), PD, network EUR/kWh, total of the invoice without TV fee
INVOICES = {
    (2026, 7): ((0.15420, 0.16938, 0.15226), (155.56, 133.29, 167.71), 0.02556, 0.04788, 166.58),
    (2026, 8): ((0.17452, 0.20435, 0.17172), (118.06, 98.94, 168.90), 0.02556, 0.047862, 154.99),
    (2026, 9): ((0.20635, 0.22758, 0.19702), (101.97, 77.00, 103.12), 0.02556, 0.047894, 128.81),
    (2026, 10): ((0.23339, 0.25214, 0.21588), (109.24, 92.72, 88.51), 0.01773, 0.047922, 138.36),
}


@pytest.mark.parametrize("year_month", INVOICES)
def test_elit_invoice(render, year_month):
    pun, kwh, pd, network, invoice = INVOICES[year_month]
    data = yaml.safe_load(ELIT)
    data["vars"].update(pd=pd, network=network)
    contract = parse_contract(data)
    result = bill(contract, render, year=year_month[0], month=year_month[1], days_in_month=31,
                  kwh_by_band=dict(zip(("F1", "F2", "F3"), kwh)),
                  averages=dict(zip(("F1", "F2", "F3"), pun)))
    assert result["total"] == pytest.approx(invoice, abs=0.03)


def test_monthly_fees_scale_with_the_month(render):
    contract = parse_contract(yaml.safe_load(ELIT))
    half = bill(contract, render, year=2026, month=11, days_in_month=30, kwh_by_band={},
                averages={}, month_fraction=0.5)
    assert half["taxable"] == pytest.approx((5.76 + 1.92 + 1.975556 * 4.5 + 3.75) / 2, abs=1e-3)


def test_met_flat_example(render):
    met = parse_contract({"pun": "quarter_hourly", "vars": {"cdispd": 0.016776},
                          "per_kwh": {"energy": "{{ pun * 1.10 + cdispd }}"},
                          "per_month": {"supplier": "{{ 72 / 12 + 1 }}"}, "vat": 0.10})
    assert met.interval_minutes == 15
    result = bill(met, render, year=2026, month=12, days_in_month=31, kwh_by_band={"F1": 10},
                  averages={}, interval_costs={"energy": 2.5})
    assert result["total"] == pytest.approx((2.5 + 7) * 1.1)


@pytest.mark.parametrize("data, message", [
    ({"pun": "weekly", "per_kwh": {"a": 1}}, "pun must be"),
    ({"pun": "hourly", "per_kwh": {}}, "at least one"),
    ({"pun": "hourly", "per_kwh": {"a": [1]}}, "number or a template"),
    ({"pun": "hourly", "per_kwh": {"a": 1}, "vat": 10}, "fraction"),
    ({"pun": "hourly", "per_kwh": {"a": 1}, "spread": 1}, "unknown keys"),
])
def test_invalid_contracts(data, message):
    with pytest.raises(ContractError, match=message):
        parse_contract(data)


def test_examples_are_valid(render):
    from pathlib import Path
    for path in (Path(__file__).parent.parent / "examples").glob("*.yaml"):
        contract = parse_contract(yaml.safe_load(path.read_text()))
        result = bill(contract, render, year=2026, month=12, days_in_month=31,
                      kwh_by_band={"F1": 100, "F2": 100, "F3": 100},
                      averages={"F1": 0.2, "F2": 0.2, "F3": 0.2, "mono": 0.2}, interval_costs={})
        assert result["total"] > 0, path.name
