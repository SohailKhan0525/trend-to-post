"""Read current Microsoft retail meters for a single always-on Qofeno VM."""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


ENDPOINT = "https://prices.azure.com/api/retail/prices"
# Each meter has its own unit. VM entries identify the exact non-Windows base meter.
METERS = (
    ("B2ats_v2 Linux", "Virtual Machines", "B2ats v2", "1hour", "Standard_B2ats_v2"),
    ("B1s Linux (alternative)", "Virtual Machines", "B1s", "1hour", "Standard_B1s"),
    ("P6 64 GiB Premium SSD LRS", "Storage", "P6 LRS Disk", "1month", None),
    ("Standard static IPv4", "Virtual Network", "Standard IPv4 Static Public IP", "1hour", None),
    ("Standard vault secret operations", "Key Vault", "Operations", "10k", None),
)


def retail_rows(location, currency, spec):
    _, service, meter, _, vm = spec
    filters = [f"armRegionName eq '{location}'", "priceType eq 'Consumption'",
               f"serviceName eq '{service}'"]
    if vm:
        filters.append(f"armSkuName eq '{vm}'")
    elif service != "Key Vault":
        filters.append(f"meterName eq '{meter}'")
    url = ENDPOINT + "?" + urlencode({"api-version": "2023-01-01-preview",
                                      "currencyCode": f"'{currency}'", "$filter": " and ".join(filters)})
    seen = set()
    while url:
        if urlsplit(url).scheme != "https" or urlsplit(url).netloc != "prices.azure.com" or url in seen:
            raise ValueError("invalid or repeated Microsoft pricing page")
        seen.add(url)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "qofeno-retail-diagnostic/1"})
        with urlopen(request, timeout=25) as response:
            page = json.load(response, parse_float=Decimal)
        yield from page["Items"]
        url = page.get("NextPageLink")


def select_price(rows, location, currency, spec, now):
    _, service, meter, unit, vm = spec
    candidates = {}
    observed = []
    for row in rows:
        if (row["armRegionName"] != location or row["currencyCode"] != currency
                or row["serviceName"] != service
                or row["type"] != "Consumption" or row["tierMinimumUnits"] != 0
                or re.sub(r"[ /]", "", row["unitOfMeasure"]).casefold() != unit):
            continue
        observed.append(row)
        meter_name = row["meterName"].casefold()
        if service == "Key Vault":
            if (row["productName"].casefold() != "key vault" or row["skuName"].casefold() != "standard"
                    or meter_name not in ("operations", "standard operations", "secret operations")):
                continue
        elif meter_name != meter.casefold():
            continue
        if vm:
            series = {"Standard_B2ats_v2": "Virtual Machines Basv2 Series", "Standard_B1s": "Virtual Machines BS Series"}
            if row["armSkuName"] != vm or row["productName"] != series[vm] or row["skuName"].casefold() != meter.casefold():
                continue
        if service == "Storage" and row["productName"] != "Premium SSD Managed Disks":
            continue
        if service == "Virtual Network" and row["productName"] != "IP Addresses":
            continue
        start = datetime.fromisoformat(row["effectiveStartDate"].replace("Z", "+00:00"))
        if start <= now:
            candidates.setdefault(row["meterId"], []).append((start, row))
    if len(candidates) != 1:
        raise ValueError(f"expected one matching meter, found {len(candidates)}. Regional candidates:\n" +
                         "\n".join(meter_details(row) for row in observed))
    history = next(iter(candidates.values()))
    latest = max(start for start, _ in history)
    matches = [row for start, row in history if start == latest]
    if len({row["retailPrice"] for row in matches}) != 1:
        raise ValueError("conflicting prices for current meter:\n" + "\n".join(meter_details(row) for row in matches))
    return matches[0]


def meter_details(row):
    return (f'{row["productName"]} / {row["skuName"]} / {row["meterName"]} | '
            f'{row["retailPrice"]} {row["currencyCode"]} per {row["unitOfMeasure"]} | '
            f'meter {row["meterId"]} effective {row["effectiveStartDate"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", default="centralindia")
    parser.add_argument("--currency", default="INR")
    args = parser.parse_args()
    location = args.location.lower()
    currency = args.currency.upper()
    if not re.fullmatch(r"[a-z0-9]+", location) or not re.fullmatch(r"[A-Z]{3}", currency):
        parser.error("expected an ARM location and a three-letter currency")
    now = datetime.now(timezone.utc)
    print(f"Microsoft Retail Prices: {location}, {currency}, {now.date()} UTC")
    rates = {}
    for spec in METERS:
        name = spec[0]
        try:
            row = select_price(retail_rows(location, currency, spec), location, currency, spec, now)
            rates[name] = Decimal(str(row["retailPrice"]))
            print(f"{name}: {meter_details(row)}")
        except (OSError, ValueError, KeyError) as exc:
            print(f"UNVERIFIED {name}: {exc}")
    print("Estimates use one VM and IP for 730 hours and one P6 disk for one month. "
          "Key Vault bills separately per 10,000 operations. Excludes taxes, transfer and extras. "
          "Retail prices do not establish remaining account allowances or actual billed costs.")
    preferred, alternate, disk, ip, _ = [rates.get(spec[0]) for spec in METERS]
    if ip is not None:
        for name, price in (("B2ats_v2", preferred), ("B1s", alternate)):
            if price is not None and disk is not None:
                print(f"{name} without allowances: {730 * (price + ip) + disk:.2f} {currency}/730h month.")
        print(f"With unused compute, disk and vault allowances: IP alone is {24 * ip:.2f} {currency}/day "
              f"or {730 * ip:.2f} {currency}/730h month.")
    return int(len(rates) != len(METERS))


if __name__ == "__main__":
    sys.exit(main())
