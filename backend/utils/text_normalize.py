import re
from typing import Dict

# Canonical sports alias dictionary for NBA, MLB, and top leagues
TEAM_ALIASES: Dict[str, str] = {
    # NBA
    "atlanta hawks": "atl", "hawks": "atl", "atl": "atl",
    "boston celtics": "bos", "celtics": "bos", "bos": "bos",
    "brooklyn nets": "bkn", "nets": "bkn", "bkn": "bkn", "brk": "bkn",
    "charlotte hornets": "cho", "hornets": "cho", "cho": "cho", "cha": "cho",
    "chicago bulls": "chi", "bulls": "chi", "chi": "chi",
    "cleveland cavaliers": "cle", "cavaliers": "cle", "cavs": "cle", "cle": "cle",
    "dallas mavericks": "dal", "mavericks": "dal", "mavs": "dal", "dal": "dal",
    "denver nuggets": "den", "nuggets": "den", "den": "den",
    "detroit pistons": "det", "pistons": "det", "det": "det",
    "golden state warriors": "gsw", "warriors": "gsw", "gsw": "gsw",
    "houston rockets": "hou", "rockets": "hou", "hou": "hou",
    "indiana pacers": "ind", "pacers": "ind", "ind": "ind",
    "la clippers": "lac", "los angeles clippers": "lac", "clippers": "lac", "lac": "lac",
    "los angeles lakers": "lal", "la lakers": "lal", "lakers": "lal", "lal": "lal",
    "memphis grizzlies": "mem", "grizzlies": "mem", "mem": "mem",
    "miami heat": "mia", "heat": "mia", "mia": "mia",
    "milwaukee bucks": "mil", "bucks": "mil", "mil": "mil",
    "minnesota timberwolves": "min", "timberwolves": "min", "wolves": "min", "min": "min",
    "new orleans pelicans": "nop", "pelicans": "nop", "nop": "nop", "noh": "nop",
    "new york knicks": "nyk", "knicks": "nyk", "nyk": "nyk",
    "oklahoma city thunder": "okc", "thunder": "okc", "okc": "okc",
    "orlando magic": "orl", "magic": "orl", "orl": "orl",
    "philadelphia 76ers": "phi", "76ers": "phi", "sixers": "phi", "phi": "phi",
    "phoenix suns": "phx", "suns": "phx", "phx": "phx", "pho": "phx",
    "portland trail blazers": "por", "trail blazers": "por", "blazers": "por", "por": "por",
    "sacramento kings": "sac", "kings": "sac", "sac": "sac",
    "san antonio spurs": "sas", "spurs": "sas", "sas": "sas",
    "toronto raptors": "tor", "raptors": "tor", "tor": "tor",
    "utah jazz": "uta", "jazz": "uta", "uta": "uta",
    "washington wizards": "was", "wizards": "was", "was": "was",

    # MLB
    "arizona diamondbacks": "ari", "diamondbacks": "ari", "dbacks": "ari", "ari": "ari", "az": "ari",
    "atlanta braves": "atl", "braves": "atl",
    "baltimore orioles": "bal", "orioles": "bal", "bal": "bal",
    "boston red sox": "bos", "red sox": "bos",
    "chicago white sox": "chw", "white sox": "chw", "chw": "chw", "cws": "chw",
    "chicago cubs": "chc", "cubs": "chc", "chc": "chc",
    "cincinnati reds": "cin", "reds": "cin", "cin": "cin",
    "cleveland guardians": "cle", "guardians": "cle", "indians": "cle", "cleveland indians": "cle",
    "colorado rockies": "col", "rockies": "col", "col": "col",
    "detroit tigers": "det", "tigers": "det",
    "houston astros": "hou", "astros": "hou",
    "kansas city royals": "kcr", "royals": "kcr", "kcr": "kcr", "kc": "kcr",
    "los angeles angels": "laa", "angels": "laa", "laa": "laa", "ana": "laa",
    "los angeles dodgers": "lad", "dodgers": "lad", "lad": "lad",
    "miami marlins": "mia", "marlins": "mia", "fla": "mia",
    "milwaukee brewers": "mil", "brewers": "mil",
    "minnesota twins": "min", "twins": "min",
    "new york yankees": "nyy", "yankees": "nyy", "nyy": "nyy",
    "new york mets": "nym", "mets": "nym", "nym": "nym",
    "oakland athletics": "oak", "athletics": "oak", "as": "oak", "oak": "oak",
    "philadelphia phillies": "phi", "phillies": "phi",
    "pittsburgh pirates": "pit", "pirates": "pit", "pit": "pit",
    "san diego padres": "sdp", "padres": "sdp", "sdp": "sdp", "sd": "sdp",
    "san francisco giants": "sfg", "giants": "sfg", "sfg": "sfg", "sf": "sfg",
    "seattle mariners": "sea", "mariners": "sea", "sea": "sea",
    "st louis cardinals": "stl", "st louis": "stl", "cardinals": "stl", "stl": "stl",
    "tampa bay rays": "tbr", "rays": "tbr", "tbd": "tbd", "tbr": "tbd", "tb": "tbd",
    "texas rangers": "tex", "rangers": "tex", "tex": "tex",
    "toronto blue jays": "tor", "blue jays": "tor", "jays": "tor",
    "washington nationals": "wsh", "nationals": "wsh", "nats": "wsh", "wsh": "wsh", "wsn": "wsh",

    # Football / Soccer
    "manchester united": "manchester united",
    "man united": "manchester united",
    "man utd": "manchester united",
    "manchester city": "manchester city",
    "man city": "manchester city",
    "tottenham hotspur": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "spurs": "tottenham hotspur",

    # NHL / Hockey
    "boston bruins": "bruins", "bruins": "bruins",
    "buffalo sabres": "sabres", "sabres": "sabres",
    "detroit red wings": "red_wings", "red wings": "red_wings",
    "florida panthers": "panthers", "panthers": "panthers",
    "montreal canadiens": "canadiens", "canadiens": "canadiens", "habs": "canadiens",
    "ottawa senators": "senators", "senators": "senators", "sens": "senators",
    "tampa bay lightning": "lightning", "lightning": "lightning", "bolts": "lightning",
    "toronto maple leafs": "maple_leafs", "maple leafs": "maple_leafs", "leafs": "maple_leafs",
    "carolina hurricanes": "hurricanes", "hurricanes": "hurricanes", "canes": "hurricanes",
    "columbus blue jackets": "blue_jackets", "blue jackets": "blue_jackets",
    "new jersey devils": "devils", "devils": "devils",
    "new york islanders": "islanders", "islanders": "islanders",
    "new york rangers": "rangers",
    "philadelphia flyers": "flyers", "flyers": "flyers",
    "pittsburgh penguins": "penguins", "penguins": "penguins", "pens": "penguins",
    "washington capitals": "capitals", "capitals": "capitals", "caps": "capitals",
    "chicago blackhawks": "blackhawks", "blackhawks": "blackhawks", "hawks": "blackhawks",
    "colorado avalanche": "avalanche", "avalanche": "avalanche", "avs": "avalanche",
    "dallas stars": "stars", "stars": "stars",
    "minnesota wild": "wild", "wild": "wild",
    "nashville predators": "predators", "predators": "predators", "preds": "predators",
    "st louis blues": "blues", "blues": "blues",
    "winnipeg jets": "jets", "jets": "jets",
    "anaheim ducks": "ducks", "ducks": "ducks",
    "calgary flames": "flames", "flames": "flames",
    "edmonton oilers": "oilers", "oilers": "oilers",
    "los angeles kings": "la_kings",
    "san jose sharks": "sharks", "sharks": "sharks",
    "seattle kraken": "kraken", "kraken": "kraken",
    "vancouver canucks": "canucks", "canucks": "canucks", "nucks": "canucks",
    "vegas golden knights": "golden_knights", "golden knights": "golden_knights", "knights": "golden_knights",
}

def normalize_team_name(name: str) -> str:
    """
    Normalizes a team name for cross-source matching only.
    Never use this for display — only for lookups/joins.
    """
    if not name:
        return ""
    n = name.strip().lower()
    n = re.sub(r"[^a-z0-9_ ]", "", n)
    for suffix in (" fc", " cf", " sc", " afc", " ac", " club", " team"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    n = re.sub(r"\s+", " ", n).strip()

    # Check alias canonical map
    if n in TEAM_ALIASES:
        return TEAM_ALIASES[n]

    return n
