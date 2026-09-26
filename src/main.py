
"""
main.py
-------
Entry point. Reads data/addresses.csv, parses every row, writes
output/parsed_addresses.csv, and prints a short summary to the console.

Run from the project root:
    python src/main.py
or with custom paths:
    python src/main.py --input data/addresses.csv --output output/parsed_addresses.csv
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from parser import parse_all  # noqa: E402


FIELDNAMES = [
    "id",
    "building",
    "street",
    "area",
    "city",
    "pincode",
    "flag",
    "reason",
    "raw_address",
]


def read_addresses(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # only ever use the address text itself -- any other column
            # (e.g. `difficulty`) present in the source file is ignored by
            # the parser on purpose, see parser.py docstring.
            rows.append((r["id"], r["address"]))
    return rows


def write_output(parsed, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for p in parsed:
            writer.writerow({
                "id": p.id,
                "building": p.building,
                "street": p.street,
                "area": p.area,
                "city": p.city,
                "pincode": p.pincode,
                "flag": "YES" if p.flag else "NO",
                "reason": p.reason_text(),
                "raw_address": p.raw_address,
            })


def print_summary(parsed):
    flagged = [p for p in parsed if p.flag]
    print(f"Parsed {len(parsed)} addresses.")
    print(f"Flagged: {len(flagged)}  |  Not flagged: {len(parsed) - len(flagged)}")
    print("-" * 70)
    for p in parsed:
        tag = "FLAGGED" if p.flag else "ok"
        print(f"[{p.id}] ({tag}) building={p.building!r} street={p.street!r} "
              f"area={p.area!r} city={p.city!r} pincode={p.pincode!r}")
        if p.reasons:
            print(f"        reason: {p.reason_text()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(
        os.path.dirname(__file__), "..", "data", "addresses.csv"))
    ap.add_argument("--output", default=os.path.join(
        os.path.dirname(__file__), "..", "output", "parsed_addresses.csv"))
    args = ap.parse_args()

    rows = read_addresses(args.input)
    parsed = parse_all(rows)
    write_output(parsed, args.output)
    print_summary(parsed)
    print("-" * 70)
    print(f"Output written to: {os.path.abspath(args.output)}")


if __name__ == "__main__":
    main()