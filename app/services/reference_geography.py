"""Authoritative Hoshiarpur operational geography supplied for this system."""

from ..extensions import db
from ..models import Block, Locality


REFERENCE_GEOGRAPHY = (
    ("Hoshiarpur City", True, (
        "Sunder Nagar", "Piplanwala", "Kamalpur", "Islamabad", "Fatehgarh",
        "Bahadurpur", "Subhash Nagar", "Dasmesh Nagar", "Kirti Nagar", "Purhiran",
        "Bhim Nagar", "Premgarh", "Rahimpur", "Roop Nagar", "Modeltown",
        "Tagore Nagar", "Tibba Sahib", "Deep Nagar", "Shalimar Nagar", "Central Town",
        "Police lines",
    )),
    ("Garhshankar", True, ("Ward 8 Garhshankar",)),
    ("Mukerian", True, ("Adarsh Nagar", "Bhatha Colony")),
    ("Hariana", True, ("Ram Garhiya Mohalla", "Pahari Gate Mohalla", "Delhi Gate Mohalla")),
    ("Bhunga", False, ("Bassi Wazid", "Bhunga", "Janoari", "Bassi Ballo", "Kang Mai")),
    ("Budhabar", False, ("Bhangala", "Khichian", "Hardokhanpur")),
    ("Chakowal", False, (
        "Aijowal", "Adamwal", "Singriwala", "Kakkon", "Nasrala", "Bagpur",
        "Dagana Kalan", "Bassi Daulat Khan", "Kantian", "Khadiala Sainian", "Salempur",
        "Chak Gujran", "Bassi Nau", "Maruli Brahmna", "Bulhowal",
        "Bassi Gulam Hussain", "Fatehgarh Niara",
    )),
    ("Harta Badla", False, (
        "Bajwara Kalan", "Khanaura", "Badial", "Chohal", "Rajpur Bhaian", "Ahrana Khurd",
        "Bassi Kalan", "Phuglana", "Jahan Khelan", "Mona Kalan", "Ram Colony Camp",
        "Attowal", "Badla", "Chabbewal", "Chhaoni Kalan", "Pandori Bibi", "Hukran",
        "Shergarh", "Sahri", "Tanuli",
    )),
    ("Possi", False, ("Badesron", "Birampur", "Saila Khurd", "Bora", "Kot Fatuhi", "Samundra", "Harwan")),
    ("Hajipur", False, ("Hajipur",)),
    ("Mand Mandher", False, ("Ghogra",)),
)


def load_reference_geography() -> tuple[int, int]:
    """Add missing approved block/locality records without altering existing records."""
    blocks_added = 0
    localities_added = 0
    for block_name, is_urban, locality_names in REFERENCE_GEOGRAPHY:
        block = Block.query.filter_by(name=block_name).first()
        if block is None:
            block = Block(name=block_name, is_urban=is_urban)
            db.session.add(block)
            db.session.flush()
            blocks_added += 1
        for locality_name in locality_names:
            if Locality.query.filter_by(block_id=block.id, name=locality_name).first() is None:
                db.session.add(Locality(
                    block=block,
                    name=locality_name,
                    locality_type="urban" if is_urban else "rural",
                ))
                localities_added += 1
    return blocks_added, localities_added
