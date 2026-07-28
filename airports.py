"""
Resolve a user-typed origin/destination into an IATA code the search APIs accept.

Both seats.aero (Cached Search) and SerpApi (Google Flights) accept 3-letter IATA
codes -- and, verified against the live seats.aero API 2026-07-25, metropolitan-area
codes too (e.g. NYC covers JFK/EWR/LGA, LON covers LHR/LGW/etc.). So a city search
resolves to the metro code where one exists (broadest coverage), otherwise to the
city's primary airport.

resolve() accepts either a raw 3-letter code (passed straight through) or a city
name / common alias (case-insensitive), and returns (code, label) or (None, None).
"""

from __future__ import annotations

# City / alias (lowercased) -> IATA code. Metro codes preferred over single airports.
_CITY_TO_CODE = {
    # North America (metro codes cover all their airports)
    "new york": "NYC", "nyc": "NYC", "new york city": "NYC", "manhattan": "NYC",
    "washington": "WAS", "washington dc": "WAS", "dc": "WAS",
    "chicago": "CHI",
    "los angeles": "LAX", "la": "LAX",
    "san francisco": "SFO", "sf": "SFO", "bay area": "SFO",
    "toronto": "YTO", "montreal": "YMQ", "vancouver": "YVR",
    "boston": "BOS", "miami": "MIA", "atlanta": "ATL", "dallas": "DFW",
    "houston": "IAH", "seattle": "SEA", "denver": "DEN", "las vegas": "LAS",
    "orlando": "MCO", "philadelphia": "PHL", "phoenix": "PHX", "newark": "EWR",
    "mexico city": "MEX", "cancun": "CUN",
    # North America -- US secondary/regional airports
    "nashville": "BNA", "raleigh": "RDU", "raleigh durham": "RDU", "durham": "RDU",
    "tampa": "TPA", "san diego": "SAN", "portland": "PDX", "portland oregon": "PDX",
    "portland maine": "PWM", "charlotte": "CLT", "detroit": "DTW",
    "minneapolis": "MSP", "minneapolis st paul": "MSP", "salt lake city": "SLC",
    "salt lake": "SLC", "new orleans": "MSY", "kansas city": "MCI",
    "pittsburgh": "PIT", "sacramento": "SMF", "san antonio": "SAT",
    "honolulu": "HNL", "anchorage": "ANC", "austin": "AUS",
    "indianapolis": "IND", "columbus": "CMH", "cleveland": "CLE",
    "cincinnati": "CVG", "st louis": "STL", "saint louis": "STL",
    "milwaukee": "MKE", "memphis": "MEM", "baltimore": "BWI",
    "buffalo": "BUF", "albuquerque": "ABQ", "tucson": "TUS", "omaha": "OMA",
    "louisville": "SDF", "oklahoma city": "OKC", "birmingham": "BHX",
    "birmingham alabama": "BHM", "richmond": "RIC", "norfolk": "ORF",
    "jacksonville": "JAX", "fort lauderdale": "FLL",
    "west palm beach": "PBI", "west palm": "PBI", "tallahassee": "TLH",
    "savannah": "SAV", "charleston": "CHS", "charleston sc": "CHS",
    "charleston wv": "CRW", "burlington": "BTV", "providence": "PVD",
    "hartford": "BDL", "albany": "ALB", "rochester": "ROC",
    "rochester mn": "RST", "syracuse": "SYR", "boise": "BOI",
    "spokane": "GEG", "reno": "RNO", "fresno": "FAT", "bakersfield": "BFL",
    "el paso": "ELP", "tulsa": "TUL", "des moines": "DSM",
    "grand rapids": "GRR", "dayton": "DAY", "knoxville": "TYS",
    "chattanooga": "CHA", "lexington": "LEX", "little rock": "LIT",
    "wichita": "ICT", "colorado springs": "COS", "fort myers": "RSW",
    "sarasota": "SRQ", "palm springs": "PSP", "monterey": "MRY",
    "santa barbara": "SBA", "eugene": "EUG", "bozeman": "BZN",
    "jackson hole": "JAC", "aspen": "ASE", "vail": "EGE",
    "asheville": "AVL", "myrtle beach": "MYR", "hilton head": "HHH",
    "long beach": "LGB", "oakland": "OAK", "san jose ca": "SJC",
    "ontario california": "ONT", "orange county": "SNA",
    "new haven": "HVN", "bangor": "BGR", "wilmington nc": "ILM",
    "harrisburg": "MDT", "allentown": "ABE", "dulles": "IAD",
    "madison": "MSN",
    # North America -- Canada secondary
    "calgary": "YYC", "edmonton": "YEG", "ottawa": "YOW", "winnipeg": "YWG",
    "halifax": "YHZ", "quebec city": "YQB", "victoria bc": "YYJ",
    "kelowna": "YLW", "saskatoon": "YXE", "regina": "YQR",
    "st john's": "YYT", "st johns newfoundland": "YYT",
    # North America -- Mexico secondary
    "guadalajara": "GDL", "monterrey": "MTY", "tijuana": "TIJ",
    "puerto vallarta": "PVR", "los cabos": "SJD", "cabo san lucas": "SJD",
    "cabo": "SJD", "cozumel": "CZM", "merida": "MID", "oaxaca": "OAX",
    "ixtapa": "ZIH", "zihuatanejo": "ZIH", "huatulco": "HUX",
    "mazatlan": "MZT", "veracruz": "VER", "guanajuato": "BJX",
    "leon mexico": "BJX", "la paz mexico": "LAP",
    # South America
    "buenos aires": "BUE", "sao paulo": "SAO", "são paulo": "SAO",
    "rio de janeiro": "RIO", "rio": "RIO", "santiago": "SCL", "lima": "LIM",
    "bogota": "BOG", "bogotá": "BOG", "cartagena": "CTG", "panama city": "PTY",
    "san jose": "SJO", "liberia": "LIR", "montego bay": "MBJ",
    "aruba": "AUA", "oranjestad": "AUA", "punta cana": "PUJ", "nassau": "NAS",
    "san juan": "SJU", "st thomas": "STT", "st maarten": "SXM",
    "quito": "UIO", "guayaquil": "GYE", "medellin": "MDE",
    "punta del este": "PDP", "montevideo": "MVD",
    "salvador": "SSA", "salvador brazil": "SSA", "recife": "REC",
    "iguazu falls": "IGR", "foz do iguacu": "IGU",
    "caracas": "CCS", "maracaibo": "MAR",
    "la paz": "LPB", "santa cruz bolivia": "VVI", "asuncion": "ASU",
    "cordoba": "COR", "mendoza": "MDZ", "bariloche": "BRC",
    "cuzco": "CUZ", "cusco": "CUZ", "arequipa": "AQP",
    "trujillo peru": "TRU", "fortaleza": "FOR", "brasilia": "BSB",
    "belo horizonte": "CNF", "porto alegre": "POA", "curitiba": "CWB",
    "manaus": "MAO", "florianopolis": "FLN", "natal": "NAT",
    "maceio": "MCZ", "georgetown guyana": "GEO", "paramaribo": "PBM",
    "port of spain": "POS", "tobago": "TAB",
    # Caribbean & Central America
    "st lucia": "UVF", "castries": "SLU", "barbados": "BGI", "bridgetown": "BGI",
    "turks and caicos": "PLS", "providenciales": "PLS", "grand turk": "GDT",
    "kingston": "KIN", "kingston jamaica": "KIN",
    "belize city": "BZE", "belize": "BZE",
    "antigua": "ANU", "grenada": "GND", "dominica": "DOM",
    "st kitts": "SKB", "nevis": "NEV", "st vincent": "SVD", "kingstown": "SVD",
    "curacao": "CUR", "willemstad": "CUR", "bonaire": "BON",
    "tortola": "EIS", "british virgin islands": "EIS", "virgin gorda": "VIJ",
    "grand cayman": "GCM", "cayman islands": "GCM", "cayman brac": "CYB",
    "little cayman": "LYB",
    "port au prince": "PAP", "haiti": "PAP",
    "santo domingo": "SDQ", "puerto plata": "POP", "samana": "AZS",
    "la romana": "LRM", "st croix": "STX",
    "guadeloupe": "PTP", "pointe a pitre": "PTP", "martinique": "FDF",
    "bermuda": "BDA", "anguilla": "AXA", "montserrat": "MNI",
    "san salvador": "SAL", "tegucigalpa": "TGU", "san pedro sula": "SAP",
    "managua": "MGA", "roatan": "RTB", "guatemala city": "GUA",
    # Europe
    "london": "LON", "paris": "PAR", "milan": "MIL", "rome": "ROM",
    "moscow": "MOW", "stockholm": "ARN",
    "madrid": "MAD", "barcelona": "BCN", "lisbon": "LIS", "porto": "OPO",
    "amsterdam": "AMS", "frankfurt": "FRA", "munich": "MUC", "berlin": "BER",
    "zurich": "ZRH", "geneva": "GVA", "vienna": "VIE", "brussels": "BRU",
    "copenhagen": "CPH", "oslo": "OSL", "helsinki": "HEL", "dublin": "DUB",
    "athens": "ATH", "istanbul": "IST", "prague": "PRG", "budapest": "BUD",
    "warsaw": "WAW", "reykjavik": "KEF", "nice": "NCE", "venice": "VCE",
    "naples": "NAP", "edinburgh": "EDI", "manchester": "MAN",
    "krakow": "KRK", "bucharest": "OTP", "sofia": "SOF", "belgrade": "BEG",
    "zagreb": "ZAG", "split": "SPU", "dubrovnik": "DBV", "malta": "MLA",
    "bordeaux": "BOD", "lyon": "LYS", "marseille": "MRS", "seville": "SVQ",
    "valencia": "VLC", "bergen": "BGO", "gothenburg": "GOT",
    "nantes": "NTE", "toulouse": "TLS", "strasbourg": "SXB", "lille": "LIL",
    "bilbao": "BIO", "malaga": "AGP", "alicante": "ALC", "ibiza": "IBZ",
    "palma": "PMI", "mallorca": "PMI", "majorca": "PMI",
    "gran canaria": "LPA", "tenerife": "TFS",
    "santorini": "JTR", "mykonos": "JMK", "crete": "HER", "heraklion": "HER",
    "rhodes": "RHO", "corfu": "CFU", "thessaloniki": "SKG",
    "ljubljana": "LJU", "sarajevo": "SJJ", "skopje": "SKP", "tirana": "TIA",
    "chisinau": "KIV", "vilnius": "VNO", "riga": "RIX", "tallinn": "TLL",
    "minsk": "MSQ", "kiev": "KBP", "kyiv": "KBP", "lviv": "LWO",
    "gdansk": "GDN", "wroclaw": "WRO", "poznan": "POZ", "katowice": "KTW",
    "basel": "BSL", "luxembourg": "LUX", "bologna": "BLQ",
    "florence": "FLR", "turin": "TRN", "verona": "VRN", "catania": "CTA",
    "palermo": "PMO", "bari": "BRI", "cagliari": "CAG",
    "san sebastian": "EAS", "a coruna": "LCG",
    "santiago de compostela": "SCQ", "faro": "FAO",
    "madeira": "FNC", "funchal": "FNC", "azores": "PDL", "ponta delgada": "PDL",
    "innsbruck": "INN", "salzburg": "SZG", "graz": "GRZ",
    "hamburg": "HAM", "dusseldorf": "DUS", "cologne": "CGN",
    "stuttgart": "STR", "nuremberg": "NUE", "hannover": "HAJ",
    "bremen": "BRE", "leipzig": "LEJ", "dresden": "DRS",
    "rotterdam": "RTM", "eindhoven": "EIN", "antwerp": "ANR",
    "groningen": "GRQ", "maastricht": "MST",
    "bristol": "BRS", "glasgow": "GLA", "newcastle": "NCL",
    "belfast": "BFS", "cardiff": "CWL", "southampton": "SOU",
    "leeds": "LBA", "liverpool": "LPL", "billund": "BLL",
    # Middle East / Africa
    "dubai": "DXB", "abu dhabi": "AUH", "doha": "DOH", "tel aviv": "TLV",
    "cairo": "CAI", "casablanca": "CMN", "johannesburg": "JNB",
    "cape town": "CPT", "nairobi": "NBO", "addis ababa": "ADD",
    "amman": "AMM", "beirut": "BEY", "marrakech": "RAK", "accra": "ACC",
    "lagos": "LOS", "dar es salaam": "DAR", "zanzibar": "ZNZ",
    "mauritius": "MRU", "seychelles": "SEZ", "kigali": "KGL",
    "kampala": "EBB", "lusaka": "LUN", "harare": "HRE", "windhoek": "WDH",
    "gaborone": "GBE", "maputo": "MPM", "tunis": "TUN", "algiers": "ALG",
    "muscat": "MCT", "kuwait city": "KWI", "manama": "BAH",
    "bahrain": "BAH", "riyadh": "RUH", "jeddah": "JED", "luxor": "LXR",
    "sharm el sheikh": "SSH", "hurghada": "HRG", "abuja": "ABV",
    "kinshasa": "FIH", "dakar": "DKR", "abidjan": "ABJ",
    "antananarivo": "TNR", "luanda": "LAD",
    # Asia / Pacific
    "tokyo": "TYO", "osaka": "OSA", "seoul": "SEL", "beijing": "BJS",
    "shanghai": "SHA",
    "hong kong": "HKG", "singapore": "SIN", "bangkok": "BKK", "bali": "DPS",
    "denpasar": "DPS", "kuala lumpur": "KUL", "jakarta": "CGK", "manila": "MNL",
    "taipei": "TPE", "delhi": "DEL", "mumbai": "BOM", "bangalore": "BLR",
    "sydney": "SYD", "melbourne": "MEL", "auckland": "AKL",
    "ho chi minh city": "SGN", "hanoi": "HAN",
    "chiang mai": "CNX", "phuket": "HKT", "cebu": "CEB", "da nang": "DAD",
    "phnom penh": "PNH", "colombo": "CMB", "kathmandu": "KTM",
    "male": "MLE", "maldives": "MLE", "christchurch": "CHC",
    "perth": "PER", "brisbane": "BNE", "fukuoka": "FUK", "sapporo": "CTS",
    "nagoya": "NGO", "busan": "PUS", "siem reap": "REP",
    "vientiane": "VTE", "yangon": "RGN", "dhaka": "DAC",
    "islamabad": "ISB", "lahore": "LHE", "karachi": "KHI",
    "chennai": "MAA", "hyderabad": "HYD", "kolkata": "CCU", "goa": "GOI",
    "kochi": "COK", "cochin": "COK", "jaipur": "JAI", "pune": "PNQ",
    "chengdu": "CTU", "chongqing": "CKG", "xian": "XIY",
    "guangzhou": "CAN", "shenzhen": "SZX", "nanjing": "NKG",
    "wuhan": "WUH", "kaohsiung": "KHH", "chiang rai": "CEI",
    "krabi": "KBV", "koh samui": "USM", "langkawi": "LGK",
    "penang": "PEN", "johor bahru": "JHB", "yogyakarta": "JOG",
    "surabaya": "SUB", "medan": "KNO", "lombok": "LOP", "cairns": "CNS",
    "gold coast": "OOL", "adelaide": "ADL", "darwin": "DRW",
    "hobart": "HBA", "wellington": "WLG", "queenstown": "ZQN",
    "dunedin": "DUD", "nadi": "NAN", "fiji": "NAN",
    "tahiti": "PPT", "papeete": "PPT", "guam": "GUM", "saipan": "SPN",
    "palau": "ROR", "xiamen": "XMN", "qingdao": "TAO", "dalian": "DLC",
    "kunming": "KMG", "sanya": "SYX", "harbin": "HRB", "tianjin": "TSN",
    "shenyang": "SHE", "naha": "OKA", "okinawa": "OKA", "sendai": "SDJ",
    "hiroshima": "HIJ", "niigata": "KIJ", "kagoshima": "KOJ",
    "matsuyama": "MYJ",
}


def resolve(query: str) -> tuple[str | None, str | None]:
    """Return (IATA code, human label) for a code or city name, or (None, None)."""
    if not query:
        return None, None
    q = query.strip()
    # A bare 3-letter token is already a code (airport or metro) -- pass it through.
    if len(q) == 3 and q.isalpha():
        return q.upper(), q.upper()
    code = _CITY_TO_CODE.get(q.lower())
    if code:
        return code, f"{q.strip().title()} ({code})"
    return None, None


def is_resolvable(query: str) -> bool:
    return resolve(query)[0] is not None


def options() -> list[str]:
    """Sorted "City (CODE)" labels for a searchable dropdown, deduped by code.

    The first alias listed for each code in _CITY_TO_CODE is its canonical name.
    """
    canonical: dict[str, str] = {}
    for name, code in _CITY_TO_CODE.items():
        canonical.setdefault(code, f"{name.title()} ({code})")
    return sorted(canonical.values())


def code_from_option(option: str | None) -> str | None:
    """"New York (NYC)" -> "NYC"; tolerant of a raw code or None."""
    if not option:
        return None
    if option.endswith(")") and "(" in option:
        return option.rsplit("(", 1)[-1].rstrip(")").strip().upper()
    return option.strip().upper() or None


def resolve_input(value: str | None) -> tuple[str | None, str | None]:
    """Resolve whatever a selectbox with accept_new_options=True returns: a
    canonical "City (CODE)" dropdown option, a bare 3-letter code typed
    directly, or a city name/alias not (yet) in the dropdown. Returns
    (IATA code, label) or (None, None) if the free-typed text isn't
    recognizable as either a code or a known alias."""
    if not value:
        return None, None
    v = value.strip()
    if v.endswith(")") and "(" in v:
        # Selected from the dropdown (or the user typed the full "City (CODE)" text).
        return code_from_option(v), v
    # Free-typed text: either a bare IATA code or an unlisted city name/alias.
    return resolve(v)


def option_from_code(code: str | None) -> str | None:
    """"SFO" -> "San Francisco (SFO)" if it's in the dropdown, else None."""
    if not code:
        return None
    code = code.strip().upper()
    for opt in options():
        if code_from_option(opt) == code:
            return opt
    return None


# The canonical dropdown label for New York, used as the default origin.
DEFAULT_ORIGIN_OPTION = "New York (NYC)"
