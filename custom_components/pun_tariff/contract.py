"""Electricity contract described in YAML, and the bill computed from it.

A contract has components in EUR/kWh and in EUR/month, before VAT. Each value is a number or a
Home Assistant template; templates see the variables below plus the contract's own `vars`.

    name: ELIT StartMyHome
    pun: monthly_bands          # monthly_bands | monthly_flat | hourly | quarter_hourly
    vars:
      spread: 0.018
      pd: 0.01773
    per_kwh:
      energy: "{{ (pun + spread) * 1.10 + pd - 0.0025 }}"
      network: 0.047922
      excise: 0.0227
    per_month:
      supplier: 5.76
      network_fixed: 1.92
      power: "{{ 1.975556 * 4.5 }}"
      other: "{{ 2.50 + 1.50 - 0.25 }}"
    vat: 0.10

Template variables:
    pun                 PUN applied to the kWh being priced (EUR/kWh): the monthly band average
                        (monthly_bands), the monthly average of all hours (monthly_flat), or the
                        price of the hour / quarter hour (hourly, quarter_hourly)
    band                "F1", "F2" or "F3"
    pun_f1, pun_f2, pun_f3, pun_mono   monthly averages so far
    kwh_month           kWh of the month so far
    year, month, days_in_month

Monthly modes price each band's kWh at the end with the month's averages, so the month total
moves while the averages settle and is final when the month is over. Interval modes price each
hour / quarter hour when it ends, with that interval's price; their `per_kwh` values are
evaluated once per interval.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .bands import BANDS

MONTHLY_BANDS = "monthly_bands"
MONTHLY_FLAT = "monthly_flat"
HOURLY = "hourly"
QUARTER_HOURLY = "quarter_hourly"
PUN_MODES = (MONTHLY_BANDS, MONTHLY_FLAT, HOURLY, QUARTER_HOURLY)
INTERVAL_MINUTES = {HOURLY: 60, QUARTER_HOURLY: 15}

Renderer = Callable[[Any, Mapping[str, Any]], float]


class ContractError(ValueError):
    """The contract YAML is not valid."""


@dataclass
class Contract:
    pun: str
    per_kwh: dict[str, Any]
    per_month: dict[str, Any]
    vat: float
    name: str = ""
    vars: dict[str, float] = field(default_factory=dict)

    @property
    def interval_minutes(self) -> int | None:
        return INTERVAL_MINUTES.get(self.pun)


def parse_contract(data: Any) -> Contract:
    """Validate the YAML mapping of a contract."""
    if not isinstance(data, Mapping):
        raise ContractError("the contract must be a YAML mapping")
    unknown = set(data) - {"name", "pun", "vars", "per_kwh", "per_month", "vat"}
    if unknown:
        raise ContractError(f"unknown keys: {', '.join(sorted(unknown))}")
    pun = data.get("pun")
    if pun not in PUN_MODES:
        raise ContractError(f"pun must be one of: {', '.join(PUN_MODES)}")
    per_kwh, per_month = data.get("per_kwh") or {}, data.get("per_month") or {}
    for key, section in (("per_kwh", per_kwh), ("per_month", per_month)):
        if not isinstance(section, Mapping):
            raise ContractError(f"{key} must be a mapping of name: number or template")
        for name, value in section.items():
            if not isinstance(value, (int, float, str)) or isinstance(value, bool):
                raise ContractError(f"{key}.{name} must be a number or a template")
    if not per_kwh:
        raise ContractError("per_kwh needs at least one component")
    contract_vars = data.get("vars") or {}
    if not isinstance(contract_vars, Mapping) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in contract_vars.values()):
        raise ContractError("vars must be a mapping of name: number")
    vat = data.get("vat", 0)
    if not isinstance(vat, (int, float)) or not 0 <= vat < 1:
        raise ContractError("vat must be a fraction, for example 0.10")
    return Contract(pun=pun, per_kwh=dict(per_kwh), per_month=dict(per_month), vat=float(vat),
                    name=str(data.get("name", "")), vars={k: float(v) for k, v in contract_vars.items()})


def template_variables(contract: Contract, *, pun: float, band: str, averages: Mapping[str, float],
                       kwh_month: float, year: int, month: int, days_in_month: int) -> dict[str, Any]:
    return {**contract.vars, "pun": pun, "band": band, "pun_f1": averages.get("F1", 0.0),
            "pun_f2": averages.get("F2", 0.0), "pun_f3": averages.get("F3", 0.0),
            "pun_mono": averages.get("mono", 0.0), "kwh_month": kwh_month, "year": year,
            "month": month, "days_in_month": days_in_month}


def rates(contract: Contract, render: Renderer, variables: Mapping[str, Any]) -> dict[str, float]:
    """EUR/kWh of each per_kwh component."""
    return {name: float(render(value, variables)) for name, value in contract.per_kwh.items()}


def monthly_fees(contract: Contract, render: Renderer, variables: Mapping[str, Any]) -> dict[str, float]:
    """EUR/month of each per_month component."""
    return {name: float(render(value, variables)) for name, value in contract.per_month.items()}


def bill(contract: Contract, render: Renderer, *, year: int, month: int, days_in_month: int,
         kwh_by_band: Mapping[str, float], averages: Mapping[str, float],
         interval_costs: Mapping[str, float] | None = None, month_fraction: float = 1.0) -> dict[str, Any]:
    """Bill of a month (EUR).

    Monthly modes price `kwh_by_band` with `averages`; interval modes take the per_kwh costs
    already accumulated per component in `interval_costs`. `month_fraction` scales the monthly
    fees (the share of the month elapsed, 1 for a closed month).
    """
    kwh_month = sum(kwh_by_band.get(b, 0.0) for b in BANDS)
    common = {"averages": averages, "kwh_month": kwh_month, "year": year, "month": month,
              "days_in_month": days_in_month}
    energy: dict[str, float] = {name: 0.0 for name in contract.per_kwh}
    if contract.interval_minutes is None:
        for band in BANDS:
            kwh = kwh_by_band.get(band, 0.0)
            pun = averages.get(band if contract.pun == MONTHLY_BANDS else "mono", 0.0)
            for name, rate in rates(contract, render, template_variables(contract, pun=pun, band=band, **common)).items():
                energy[name] += kwh * rate
    else:
        for name in energy:
            energy[name] = float((interval_costs or {}).get(name, 0.0))
    fees = {name: value * month_fraction for name, value in monthly_fees(
        contract, render, template_variables(contract, pun=averages.get("mono", 0.0), band="", **common)).items()}
    taxable = sum(energy.values()) + sum(fees.values())
    vat = taxable * contract.vat
    return {"kwh": round(kwh_month, 3), "kwh_by_band": {b: round(kwh_by_band.get(b, 0.0), 3) for b in BANDS},
            "per_kwh": {k: round(v, 4) for k, v in energy.items()},
            "per_month": {k: round(v, 4) for k, v in fees.items()},
            "taxable": round(taxable, 4), "vat": round(vat, 4), "total": round(taxable + vat, 4)}
