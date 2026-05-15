"""Canonical spellings for VehicleSubClass.

The Excel sheet has misspellings (e.g. ``Excavaters``, ``Buldozer``,
``Tranist Mixer``). We preserve the clean spellings here so that downstream
consumers see consistent values regardless of the Excel content.

Keyed by ``(product, pb_vehicle_subclass_id)``.
"""

VEHICLE_SUBCLASS_CANONICAL = {
    ("CV", 1): "Truck",
    ("CV", 2): "Heavy Truck/Semi Trailer",
    ("CV", 3): "Tipper",
    ("CV", 5): "Dumper",
    ("CV", 6): "Tractor",
    ("CV", 7): "Tanker",
    ("CV", 8): "Bulker",
    ("CV", 9): "Trailer",
    ("CV", 10): "Pick Up/Delivery Van",
    ("CV", 11): "Refrigerated Van",
    ("CV", 12): "E Rickshaw",
    ("CV", 13): "Auto Rickshaw",
    ("CV", 14): "Passenger Bus",
    ("CV", 15): "School Bus",
    ("CV", 16): "Agricultural Tractor",
    ("CV", 20): "Bulldozer",
    ("CV", 21): "Crane",
    ("CV", 22): "Garbage Van",
    ("CV", 23): "Ambulance",
    ("CV", 24): "Excavator",
    ("CV", 27): "Staff Bus",
    ("CV", 28): "Taxi",
    ("CV", 31): "Fork Lift Trucks",
    ("CV", 32): "Drilling Rigs",
    ("CV", 34): "Fire Vehicles",
    ("CV", 36): "Compressors",
    ("CV", 37): "Road Rollers",
    ("CV", 38): "Motor Grader",
    ("CV", 39): "Transit Mixer",
    ("CV", 40): "Generator Van",
    ("CV", 41): "Backhoe Loader",
    ("CV", 42): "Loader",
    ("CV", 43): "Harvester",
    ("CV", 44): "Tractor Loader",
    ("CV", 47): "Car Carrier",
    ("CV", 48): "Flat Bed Trailer",
    ("CV", 49): "Kisan Suvidha",
    ("CV", 50): "Compactor",
    ("CV", 51): "Vibrator",
    ("CV", 52): "Towing Van",
    ("CV", 53): "Tar Sprayer",
    ("CV", 54): "Jetting Machine",
    ("CV", 55): "Container",
    ("CV", 56): "Gas Tanker",
    ("CV", 57): "Car Carrier",
    ("CV", 58): "Road Sweeper",
    ("TW", 1): "Bike",
    ("TW", 2): "Scooter",
    ("TW", 3): "E-Bike",
    ("Car", 1): "Private Car",
    ("Car", 2): "Taxi",
}


INSURER_NOISE_TOKENS = (
    "LTD",
    "LIMITED",
    "COMPANY",
    "GENERAL",
    "INSURANCE",
    "CO",
)


def normalize_insurer_name(name: str) -> str:
    """Normalize insurer name for matching: uppercase, strip punctuation,
    drop generic noise tokens, collapse whitespace."""
    if not name:
        return ""
    import re

    cleaned = re.sub(r"[^\w\s]", " ", name.upper())
    tokens = [t for t in cleaned.split() if t and t not in INSURER_NOISE_TOKENS]
    return " ".join(tokens)
