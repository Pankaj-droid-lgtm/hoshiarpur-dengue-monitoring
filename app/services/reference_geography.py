"""Reviewed operational geography from the supplied Hoshiarpur source material."""

from __future__ import annotations

from hashlib import sha256

from ..extensions import db
from ..models import Block, GeographyAlias, HistoricalHighRiskCluster, Locality, SourceDocument


# Each entry is (area name, area type, approved locality/village/ward names).  Reporting
# labels with no confirmed locality relationship are deliberately kept as source aliases.
REFERENCE_GEOGRAPHY = (
    ("Hoshiarpur City", "urban", (
        "Sunder Nagar", "Piplanwala", "Kamalpur", "Islamabad", "Fatehgarh",
        "Bahadurpur", "Subhash Nagar", "Dasmesh Nagar", "Kirti Nagar", "Purhiran",
        "Bhim Nagar", "Premgarh", "Rahimpur", "Roop Nagar", "Modeltown",
        "Tagore Nagar", "Tibba Sahib", "Deep Nagar", "Shalimar Nagar", "Central Town",
        "Police lines", "Saffron City", "Sarajan Chowk", "Naloyian Chowk",
    )),
    ("Garhshankar", "urban", ("Ward 8 Garhshankar",)),
    ("Mukerian", "urban", ("Adarsh Nagar", "Bhatha Colony", "Tehsil Colony", "Talwara Road")),
    ("Hariana", "urban", ("Ram Garhiya Mohalla", "Pahari Gate Mohalla", "Delhi Gate Mohalla")),
    # These urban areas occur in the breeding-checker source, but its rows do not give a
    # locality, ward, colony, or mohalla to create beneath them.
    ("Dasuya", "urban", ()),
    ("Talwara", "urban", ()),
    ("Mahilpur", "urban", ()),
    ("Tanda", "urban", ()),
    ("Garhdiwala", "urban", ()),
    ("Bhunga", "rural", (
        "Bassi Wazid", "Bhunga", "Janoari", "Bassi Ballo", "Kang Mai", "Malhewal",
        "Phambra", "Mehangerwal", "Kabirpur", "Kabirpur Shekan",
    )),
    ("Budhabar", "rural", (
        "Bhangala", "Khichian", "Hardokhanpur", "Mehtabpur", "Miani Malah",
        "Dharampur", "Khanpur", "Abdullahpur",
    )),
    ("Chakowal", "rural", (
        "Aijowal", "Adamwal", "Singriwala", "Kakkon", "Nasrala", "Bagpur",
        "Dagana Kalan", "Bassi Daulat Khan", "Kantian", "Khadiala Sainian", "Salempur",
        "Chak Gujran", "Bassi Nau", "Maruli Brahmna", "Bulhowal",
        "Bassi Gulam Hussain", "Fatehgarh Niara", "Alowal", "Muradpur", "Muradpur Naria", "Pandori Bava",
        "Sonalika",
    )),
    ("Harta Badla", "rural", (
        "Bajwara Kalan", "Khanaura", "Badial", "Chohal", "Rajpur Bhaian", "Ahrana Khurd",
        "Bassi Kalan", "Phuglana", "Jahan Khelan", "Mona Kalan", "Ram Colony Camp",
        "Attowal", "Badla", "Chabbewal", "Chhaoni Kalan", "Pandori Bibi", "Hukran",
        "Shergarh", "Sahri", "Tanuli", "Manjhi", "Mannan",
    )),
    ("Possi", "rural", (
        "Badesron", "Birampur", "Saila Khurd", "Bora", "Kot Fatuhi", "Samundra", "Harwan",
        "Sihwal", "Basiala", "Moranwali", "Darapur", "Rampur Bilron", "Sadewal",
    )),
    ("Hajipur", "rural", ("Hajipur",)),
    ("Mand Mandher", "rural", ("Ghogra", "Aima Mangat")),
    # Paldi is a supplied reporting/operational area. No supplied source safely relates a
    # named village to it, so it intentionally has no dropdown locality yet.
    ("Paldi", "rural", ()),
)


# These are clear spelling/source variants, or retained reporting labels whose relationship
# is not safe enough to turn into a new operational dropdown option.
SOURCE_ALIASES = (
    ("URBAN HSP", "Hoshiarpur City", None),
    ("HOSHIARPUR", "Hoshiarpur City", None),
    ("Hoshiarpur (urban)", "Hoshiarpur City", None),
    ("Urban Hoshiarpur", "Hoshiarpur City", None),
    ("GARHSHANKER", "Garhshankar", None),
    ("GARHDIWAL", "Garhdiwala", None),
    ("Gardhiwala", "Garhdiwala", None),
    ("Budhabarh", "Budhabar", None),
    ("Mand Bhander", "Mand Mandher", None),
    ("Mand Pandher", "Mand Mandher", None),
    ("Purhira", "Hoshiarpur City", "Purhiran"),
    ("Model Town", "Hoshiarpur City", "Modeltown"),
    ("Baghpur", "Chakowal", "Bagpur"),
    ("nasrala hsp", "Chakowal", "Nasrala"),
    ("pandori bawha hsp", "Chakowal", "Pandori Bava"),
    ("pandori bava", "Chakowal", "Pandori Bava"),
    ("muradpur nariala hsp", "Chakowal", "Muradpur Naria"),
    ("MALHEWAL, DASUYA", "Bhunga", "Malhewal"),
    ("ABDULAPUR", "Budhabar", "Abdullahpur"),
    ("manan hsp", "Harta Badla", "Mannan"),
    ("Tanda (rural)", None, None),
    ("Hoshiarpur-1", None, None),
    ("Hoshiarpur-2", None, None),
    ("Sham chourasi", None, None),
    ("Binewal", None, None),
    ("Kamahi Devi", None, None),
    ("Bhol Kalota", None, None),
)


# The supplied high-risk photograph has no per-locality counts. The PDF gives only grouped
# counts, so those counts are not copied to individual localities.
HIGH_RISK_LOCATIONS = tuple(
    (area_type, block_name, locality_name)
    for block_name, area_type, locality_names in REFERENCE_GEOGRAPHY
    for locality_name in locality_names
    if (block_name, locality_name) in {
        ("Hoshiarpur City", "Sunder Nagar"), ("Hoshiarpur City", "Piplanwala"),
        ("Hoshiarpur City", "Kamalpur"), ("Hoshiarpur City", "Islamabad"),
        ("Hoshiarpur City", "Fatehgarh"), ("Hoshiarpur City", "Bahadurpur"),
        ("Hoshiarpur City", "Subhash Nagar"), ("Hoshiarpur City", "Dasmesh Nagar"),
        ("Hoshiarpur City", "Kirti Nagar"), ("Hoshiarpur City", "Purhiran"),
        ("Hoshiarpur City", "Bhim Nagar"), ("Hoshiarpur City", "Premgarh"),
        ("Hoshiarpur City", "Rahimpur"), ("Hoshiarpur City", "Roop Nagar"),
        ("Hoshiarpur City", "Modeltown"), ("Hoshiarpur City", "Tagore Nagar"),
        ("Hoshiarpur City", "Tibba Sahib"), ("Hoshiarpur City", "Deep Nagar"),
        ("Hoshiarpur City", "Shalimar Nagar"), ("Hoshiarpur City", "Central Town"),
        ("Hoshiarpur City", "Police lines"), ("Hoshiarpur City", "Saffron City"),
        ("Garhshankar", "Ward 8 Garhshankar"), ("Mukerian", "Adarsh Nagar"),
        ("Mukerian", "Bhatha Colony"), ("Mukerian", "Tehsil Colony"),
        ("Mukerian", "Talwara Road"), ("Hariana", "Delhi Gate Mohalla"),
        ("Bhunga", "Bassi Wazid"), ("Bhunga", "Bhunga"), ("Bhunga", "Janoari"),
        ("Bhunga", "Bassi Ballo"), ("Bhunga", "Kang Mai"), ("Bhunga", "Malhewal"),
        ("Bhunga", "Phambra"), ("Bhunga", "Kabirpur Shekan"),
        ("Budhabar", "Bhangala"), ("Budhabar", "Khichian"), ("Budhabar", "Hardokhanpur"),
        ("Budhabar", "Khanpur"), ("Budhabar", "Abdullahpur"),
        ("Chakowal", "Aijowal"), ("Chakowal", "Adamwal"), ("Chakowal", "Singriwala"),
        ("Chakowal", "Kakkon"), ("Chakowal", "Nasrala"), ("Chakowal", "Bagpur"),
        ("Chakowal", "Dagana Kalan"), ("Chakowal", "Bassi Daulat Khan"),
        ("Chakowal", "Kantian"), ("Chakowal", "Khadiala Sainian"),
        ("Chakowal", "Salempur"), ("Chakowal", "Chak Gujran"), ("Chakowal", "Bassi Nau"),
        ("Chakowal", "Maruli Brahmna"), ("Chakowal", "Bulhowal"),
        ("Chakowal", "Bassi Gulam Hussain"), ("Chakowal", "Fatehgarh Niara"),
        ("Chakowal", "Muradpur"), ("Chakowal", "Muradpur Naria"), ("Chakowal", "Pandori Bava"),
        ("Harta Badla", "Bajwara Kalan"), ("Harta Badla", "Khanaura"),
        ("Harta Badla", "Badial"), ("Harta Badla", "Chohal"), ("Harta Badla", "Rajpur Bhaian"),
        ("Harta Badla", "Ahrana Khurd"), ("Harta Badla", "Bassi Kalan"),
        ("Harta Badla", "Phuglana"), ("Harta Badla", "Jahan Khelan"),
        ("Harta Badla", "Mona Kalan"), ("Harta Badla", "Ram Colony Camp"),
        ("Harta Badla", "Attowal"), ("Harta Badla", "Badla"), ("Harta Badla", "Chabbewal"),
        ("Harta Badla", "Chhaoni Kalan"), ("Harta Badla", "Pandori Bibi"),
        ("Harta Badla", "Hukran"), ("Harta Badla", "Shergarh"), ("Harta Badla", "Sahri"),
        ("Harta Badla", "Tanuli"), ("Harta Badla", "Manjhi"), ("Harta Badla", "Mannan"),
        ("Possi", "Badesron"), ("Possi", "Birampur"), ("Possi", "Saila Khurd"),
        ("Possi", "Bora"), ("Possi", "Kot Fatuhi"), ("Possi", "Samundra"),
        ("Possi", "Harwan"), ("Possi", "Darapur"), ("Possi", "Rampur Bilron"),
        ("Possi", "Sadewal"), ("Hajipur", "Hajipur"), ("Mand Mandher", "Ghogra"),
    }
)


def area_type_for_block(block: Block) -> str:
    return "urban" if block.is_urban else "rural"


def load_reference_geography() -> tuple[int, int, int, int]:
    """Add reviewed geography and high-risk reference rows without changing existing records."""
    blocks_added = localities_added = aliases_added = high_risk_added = 0
    blocks_by_name: dict[str, Block] = {}
    for block_name, area_type, locality_names in REFERENCE_GEOGRAPHY:
        block = Block.query.filter_by(name=block_name).first()
        if block is None:
            block = Block(name=block_name, is_urban=area_type == "urban")
            db.session.add(block)
            db.session.flush()
            blocks_added += 1
        blocks_by_name[block_name] = block
        for locality_name in locality_names:
            if Locality.query.filter_by(block_id=block.id, name=locality_name).first() is None:
                db.session.add(Locality(block=block, name=locality_name, locality_type=area_type))
                localities_added += 1
    db.session.flush()

    for source_name, block_name, locality_name in SOURCE_ALIASES:
        block = blocks_by_name.get(block_name) if block_name else None
        locality = Locality.query.filter_by(block_id=block.id, name=locality_name).first() if block and locality_name else None
        if GeographyAlias.query.filter_by(source_name=source_name, source_document_id=None).first() is None:
            db.session.add(GeographyAlias(
                source_name=source_name, block=block, locality=locality,
                review_status="approved" if block or locality else "pending",
            ))
            aliases_added += 1

    source_payload = "|".join(f"{area}:{block}:{locality}" for area, block, locality in HIGH_RISK_LOCATIONS)
    digest = sha256(source_payload.encode("utf-8")).hexdigest()
    source = SourceDocument.query.filter_by(sha256=digest).first()
    if source is None:
        source = SourceDocument(
            original_filename="Reviewed supplied Hoshiarpur high-risk references",
            sha256=digest,
            source_type="reference_geography",
        )
        db.session.add(source)
        db.session.flush()
    for source_row, (area_type, block_name, locality_name) in enumerate(HIGH_RISK_LOCATIONS, 1):
        if HistoricalHighRiskCluster.query.filter_by(source_document_id=source.id, source_row=source_row).first() is None:
            db.session.add(HistoricalHighRiskCluster(
                source_document_id=source.id, source_row=source_row, area_type_raw=area_type,
                block_raw=block_name, locality_raw=locality_name,
            ))
            high_risk_added += 1
    return blocks_added, localities_added, aliases_added, high_risk_added
