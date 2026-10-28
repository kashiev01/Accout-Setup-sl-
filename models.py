"""CSV row parsing and validation into Client objects."""

import csv
from dataclasses import dataclass, field

from packs import parse_packs_field

VALID_MODES = {"quicklink", "dashboard_manager", "osint_start", "ai_user", "ai_company"}

# Free-text mode phrasings from request forms that don't map 1:1 onto a VALID_MODES
# token by simple lower()+space->underscore. Matched case-insensitively on the raw
# (space-separated, not yet underscored) phrase.
MODE_ALIASES = {
    "ai mode (for user only)": "ai_user",
    "ai mode (for whole company)": "ai_company",
}


def normalize_mode(raw: str) -> str:
    s = raw.strip().lower()
    return MODE_ALIASES.get(s, s.replace(" ", "_"))

# CSV license_type -> CrimeWall #id_license_type option value ("paid" is CW's "license").
LICENSE_TYPE_MAP = {
    "trial":    "1",
    "paid":     "2",
    "partner":  "3",
    "internal": "4",
}

# SL API rows only accept trial|paid (they pick the token's Plan, see slapi.SLAPI_PLANS).
SLAPI_LICENSE_TYPES = {"trial", "paid"}


def normalize_license(raw: str) -> str:
    """'Paid License' / 'Trial' / 'Internal License' -> 'paid' / 'trial' / 'internal'."""
    s = raw.strip().lower()
    if s.endswith(" license"):
        s = s[: -len(" license")].strip()
    return s


@dataclass
class Client:
    product: str
    full_name: str
    email: str
    company: str
    license_type: str
    start_date: str
    end_date: str
    modes: list[str]
    packs: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)
    company_id: str = "0"
    points: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "Client":
        product = row.get("product", "Crimewall").strip()

        if product == "SL API":
            packs = parse_packs_field(row.get("packs", ""))
            start_date = row.get("start_date", "").strip()
            if not start_date:
                raise ValueError("start_date is required for SL API rows (needed for the token's contract start date)")
            company_id = row.get("company_id", "").strip() or "0"
            lt = row.get("license_type", "").strip().lower()
            if lt not in SLAPI_LICENSE_TYPES:
                raise ValueError(f"Unknown license_type '{lt}' for SL API row. Valid: {sorted(SLAPI_LICENSE_TYPES)}")
            return cls(
                product=product,
                full_name=row["full_name"].strip(),
                email=row["Email"].strip(),
                company=row.get("Company name", "").strip(),
                license_type=lt,
                start_date=start_date,
                end_date=row["end_date"].strip(),
                modes=[],
                packs=packs,
                company_id=company_id,
                points=row.get("points", "").strip() or "500",
            )

        raw_modes = row.get("modes", "")
        modes = [normalize_mode(m) for m in raw_modes.split(",") if m.strip() and m.strip().lower() != "none"]
        unknown = set(modes) - VALID_MODES
        if unknown:
            raise ValueError(f"Unknown modes: {unknown}")

        lt = row["license_type"].strip().lower()
        if lt not in LICENSE_TYPE_MAP:
            raise ValueError(f"Unknown license_type '{row['license_type']}'. Valid: {list(LICENSE_TYPE_MAP)}")

        packs = parse_packs_field(row.get("packs", ""))

        company_id = row.get("company_id", "").strip() or "0"

        return cls(
            product=product,
            full_name=row["full_name"].strip(),
            email=row["Email"].strip(),
            company=row["Company name"].strip(),
            license_type=lt,
            start_date=row["start_date"].strip(),
            end_date=row["end_date"].strip(),
            modes=modes,
            packs=packs,
            company_id=company_id,
        )


def load_clients(csv_path: str) -> list[Client]:
    clients = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for i, row in enumerate(reader, start=2):
            try:
                clients.append(Client.from_row(row))
            except (KeyError, ValueError) as e:
                print(f"[SKIP] Row {i}: {e}")
    return clients
