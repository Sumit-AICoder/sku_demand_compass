"""NARP / AESR agro-climatic zones and sub-zones for the pilot states.

The agro-climatic axis of an archetype is the district's NARP (National Agricultural
Research Project) sub-zone rather than a clustered proxy. Only the sub-zones that occur
in Punjab, Madhya Pradesh and Maharashtra are carried here. Each district is assigned to
one sub-zone from agronomic geography (region + soil + length-of-growing-period); the
assignment is cross-checked against real IMD rainfall in the pipeline log.

This is a best-effort mapping to the published NARP scheme, not an authoritative
district gazetteer -- a few border districts are judgement calls.
"""
from __future__ import annotations

# sub-zone id -> {zone, zone_name, subzone_name, lgp (length of growing period, days)}
SUBZONES: dict[str, dict] = {
    "2.3":  {"zone": "2",  "zone_name": "Western Plain (hot arid)",
             "subzone_name": "SW Punjab plain", "lgp": "60-90"},
    "4.1":  {"zone": "4",  "zone_name": "Northern Plain (hot semi-arid)",
             "subzone_name": "N Punjab sub-montane", "lgp": "90-120"},
    "4.2":  {"zone": "4",  "zone_name": "Northern Plain (hot semi-arid)",
             "subzone_name": "Punjab plains", "lgp": "120-150"},
    "4.3":  {"zone": "4",  "zone_name": "Central Highlands (hot semi-arid)",
             "subzone_name": "Rajasthan uplands & N Malwa", "lgp": "90-120"},
    "5.2":  {"zone": "5",  "zone_name": "Central Highlands (semi-arid)",
             "subzone_name": "W Malwa & Nimar", "lgp": "90-120"},
    "5.3":  {"zone": "5",  "zone_name": "Central Highlands (semi-arid)",
             "subzone_name": "Malwa plateau", "lgp": "120-150"},
    "6.1":  {"zone": "6",  "zone_name": "Deccan Plateau (hot semi-arid)",
             "subzone_name": "Deccan shallow black (scarcity)", "lgp": "90-120"},
    "6.2":  {"zone": "6",  "zone_name": "Deccan Plateau (hot semi-arid)",
             "subzone_name": "Marathwada medium-deep black", "lgp": "120-150"},
    "6.3":  {"zone": "6",  "zone_name": "Deccan Plateau (hot semi-arid)",
             "subzone_name": "Central Maharashtra plateau", "lgp": "120-150"},
    "6.4":  {"zone": "6",  "zone_name": "Deccan Plateau (hot semi-arid)",
             "subzone_name": "W Vidarbha deep black (cotton)", "lgp": "150-180"},
    "10.2": {"zone": "10", "zone_name": "Central Highlands (hot subhumid)",
             "subzone_name": "Vindhyan scarpland", "lgp": "150-180"},
    "10.3": {"zone": "10", "zone_name": "Central Highlands (hot subhumid)",
             "subzone_name": "Satpura & Wainganga valley", "lgp": "180-210"},
    "10.4": {"zone": "10", "zone_name": "Central Highlands (hot subhumid)",
             "subzone_name": "Bundelkhand uplands", "lgp": "150-180"},
    "19.1": {"zone": "19", "zone_name": "Western Ghats & Coastal (humid)",
             "subzone_name": "N Sahyadris", "lgp": "210-240"},
    "19.2": {"zone": "19", "zone_name": "Western Ghats & Coastal (perhumid)",
             "subzone_name": "Konkan coastal plain", "lgp": "240-270"},
}

# district -> sub-zone id (district names match geo_districts.parquet)
DISTRICT_SUBZONE: dict[str, str] = {
    # ---- Punjab ----
    "Fazilka": "2.3", "Firozpur": "2.3", "Sri Muktsar Sahib": "2.3", "Faridkot": "2.3",
    "Bathinda": "2.3", "Mansa": "2.3",
    "Pathankot": "4.1", "Gurdaspur": "4.1", "Hoshiarpur": "4.1", "Rupnagar": "4.1",
    "Shahid Bhagat Singh Nagar": "4.1",
    "Amritsar": "4.2", "Tarn Taran": "4.2", "Kapurthala": "4.2", "Jalandhar": "4.2",
    "Ludhiana": "4.2", "Moga": "4.2", "Sangrur": "4.2", "Barnala": "4.2", "Patiala": "4.2",
    "Fatehgarh Sahib": "4.2", "Sahibzada Ajit Singh Nagar": "4.2", "Malerkotla": "4.2",
    # ---- Madhya Pradesh ----
    "Gwalior": "4.3", "Morena": "4.3", "Bhind": "4.3", "Sheopur": "4.3", "Datia": "4.3",
    "Shivpuri": "4.3", "Guna": "4.3", "Ashoknagar": "4.3",
    "Indore": "5.3", "Ujjain": "5.3", "Dewas": "5.3", "Ratlam": "5.3", "Mandsaur": "5.3",
    "Neemuch": "5.3", "Shajapur": "5.3", "Agar Malwa": "5.3", "Rajgarh": "5.3",
    "Sehore": "5.3", "Bhopal": "5.3", "Vidisha": "5.3",
    "Dhar": "5.2", "Jhabua": "5.2", "Alirajpur": "5.2", "Khargone": "5.2", "Khandwa": "5.2",
    "Barwani": "5.2", "Burhanpur": "5.2",
    "Sagar": "10.4", "Damoh": "10.4", "Chhatarpur": "10.4", "Tikamgarh": "10.4",
    "Niwari": "10.4", "Panna": "10.4",
    "Rewa": "10.2", "Satna": "10.2", "Sidhi": "10.2", "Singrauli": "10.2", "Maihar": "10.2",
    "Mauganj": "10.2",
    "Narmadapuram": "10.3", "Harda": "10.3", "Betul": "10.3", "Chhindwara": "10.3",
    "Pandhurna": "10.3", "Seoni": "10.3", "Narsinghpur": "10.3", "Jabalpur": "10.3",
    "Katni": "10.3", "Raisen": "10.3", "Mandla": "10.3", "Dindori": "10.3",
    "Balaghat": "10.3", "Shahdol": "10.3", "Umaria": "10.3", "Anuppur": "10.3",
    # ---- Maharashtra ----
    "Mumbai City": "19.2", "Mumbai Suburban": "19.2", "Thane": "19.2", "Palghar": "19.2",
    "Raigad": "19.2", "Ratnagiri": "19.2", "Sindhudurg": "19.2",
    "Kolhapur": "19.1",
    "Ahmednagar": "6.1", "Solapur": "6.1", "Sangli": "6.1", "Osmanabad": "6.1",
    "Beed": "6.1", "Jalna": "6.1",
    "Pune": "6.3", "Satara": "6.3", "Nashik": "6.3", "Dhule": "6.3", "Jalgaon": "6.3",
    "Nandurbar": "6.3",
    "Aurangabad": "6.2", "Latur": "6.2", "Nanded": "6.2", "Parbhani": "6.2", "Hingoli": "6.2",
    "Amravati": "6.4", "Akola": "6.4", "Buldhana": "6.4", "Washim": "6.4", "Yavatmal": "6.4",
    "Nagpur": "10.3", "Wardha": "10.3", "Bhandara": "10.3", "Gondia": "10.3",
    "Chandrapur": "10.3", "Gadchiroli": "10.3",
}


def subzone_of(district: str) -> str:
    return DISTRICT_SUBZONE.get(district, "")


def meta(subzone_id: str) -> dict:
    return SUBZONES.get(subzone_id, {"zone": "", "zone_name": "Unclassified",
                                     "subzone_name": "Unclassified", "lgp": ""})
