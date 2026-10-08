import asyncio
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from backend.db.mongodb import mongo_manager
from backend.db.redis_client import redis_client
from backend.services.freshness_policy import freshness_policy

COMPETITION_CACHE_TTL_SECONDS = 43200  # 12 hours

# Authoritative baseline registry covering reference sports and competitions.
# In accordance with STEP 1 rules, baseline entries start as unconfirmed reference (supported=False, active=False)
# until authoritative provider discovery confirms availability.
DEFAULT_BASELINE_COMPETITIONS: Dict[str, List[Dict[str, Any]]] = {
    "football": [
        # Top 5 European Leagues
        {"competition_id": "premier-league", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Premier League", "country_or_region": "England", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["EPL", "English Premier League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "la-liga", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "La Liga", "country_or_region": "Spain", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["Primera Division", "LaLiga EA Sports"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "serie-a", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Serie A", "country_or_region": "Italy", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["Serie A TIM", "Italian Serie A"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "bundesliga", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Bundesliga", "country_or_region": "Germany", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["German Bundesliga"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "ligue-1", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Ligue 1", "country_or_region": "France", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["Ligue 1 McDonald's", "French Ligue 1"], "discovery_status": "unconfirmed_reference"},
        # European & Continental Cups
        {"competition_id": "champions-league", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "UEFA Champions League", "country_or_region": "Europe", "level": "continental_tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["UCL", "Champions League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "europa-league", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "UEFA Europa League", "country_or_region": "Europe", "level": "continental_tier_2", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["UEL", "Europa League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "conference-league", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "UEFA Conference League", "country_or_region": "Europe", "level": "continental_tier_3", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["UECL", "Conference League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "copa-libertadores", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Copa Libertadores", "country_or_region": "South America", "level": "continental_tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["CONMEBOL Libertadores"], "discovery_status": "unconfirmed_reference"},
        # Primary Domestic Leagues
        {"competition_id": "mls", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Major League Soccer (MLS)", "country_or_region": "USA/Canada", "level": "tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["MLS"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "primeira-liga", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Liga Portugal", "country_or_region": "Portugal", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Primeira Liga", "Liga Portugal Betclic"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "eredivisie", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Eredivisie", "country_or_region": "Netherlands", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Dutch Eredivisie"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "scottish-premiership", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Scottish Premiership", "country_or_region": "Scotland", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["SPFL", "Scottish Premiership"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "belgian-pro-league", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Belgian Pro League", "country_or_region": "Belgium", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Jupiler Pro League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "super-lig", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Süper Lig", "country_or_region": "Turkey", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Turkish Süper Lig", "Trendyol Süper Lig"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "saudi-pro-league", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Saudi Pro League", "country_or_region": "Saudi Arabia", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Roshn Saudi League", "SPL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "championship", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "EFL Championship", "country_or_region": "England", "level": "tier_2", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Championship"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "brasileirao", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Campeonato Brasileiro Série A", "country_or_region": "Brazil", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Brasileirao", "Serie A Brazil"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "argentina-primera", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Argentine Primera División", "country_or_region": "Argentina", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Liga Profesional Argentina"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "mexico-liga-mx", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Liga MX", "country_or_region": "Mexico", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Liga BBVA MX"], "discovery_status": "unconfirmed_reference"},
        # International Competitions
        {"competition_id": "world-cup", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "FIFA World Cup", "country_or_region": "International", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["World Cup", "FIFA World Cup 2026"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "uefa-nations-league", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "UEFA Nations League", "country_or_region": "Europe", "level": "international", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["Nations League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "afcon", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Africa Cup of Nations", "country_or_region": "Africa", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["AFCON", "CAN"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "copa-america", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "Copa América", "country_or_region": "Americas", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["Copa America"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "asian-cup", "provider": "machina-sports/sports-skills", "sport": "football", "competition_name": "AFC Asian Cup", "country_or_region": "Asia", "level": "international", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Asian Cup"], "discovery_status": "unconfirmed_reference"},
    ],
    "hockey": [
        # North American & International Primary
        {"competition_id": "nhl", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "National Hockey League (NHL)", "country_or_region": "USA/Canada", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["NHL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "khl", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "Kontinental Hockey League (KHL)", "country_or_region": "International", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["KHL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "shl", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "Swedish Hockey League (SHL)", "country_or_region": "Sweden", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["SHL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "liiga", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "Liiga", "country_or_region": "Finland", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["SM-Liiga", "Finnish Liiga"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "national-league-swiss", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "National League (NL)", "country_or_region": "Switzerland", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["Swiss NL", "National League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "del", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "Deutsche Eishockey Liga (DEL)", "country_or_region": "Germany", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["DEL", "German DEL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "czech-extraliga", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "Czech Extraliga", "country_or_region": "Czechia", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["ELH", "Tipsport Extraliga"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "ahl", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "American Hockey League (AHL)", "country_or_region": "USA/Canada", "level": "tier_2", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["AHL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "echl", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "ECHL", "country_or_region": "USA/Canada", "level": "tier_3", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["ECHL", "East Coast Hockey League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "icehl", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "ICE Hockey League", "country_or_region": "Austria/Europe", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["win2day ICE Hockey League", "EBEL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "hockeyallsvenskan", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "HockeyAllsvenskan", "country_or_region": "Sweden", "level": "tier_2", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Allsvenskan"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "del2", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "DEL2", "country_or_region": "Germany", "level": "tier_2", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["DEL2"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "ncaa-hockey", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "NCAA Men's Ice Hockey", "country_or_region": "USA", "level": "college", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["NCAA Hockey", "College Hockey"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "pwhl", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "Professional Women's Hockey League (PWHL)", "country_or_region": "USA/Canada", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["PWHL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "chl", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "Champions Hockey League (CHL)", "country_or_region": "Europe", "level": "continental_tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["CHL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "iihf-world-championship", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "IIHF World Championship", "country_or_region": "International", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["IIHF Worlds"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "iihf-world-juniors", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "IIHF World Junior Championship", "country_or_region": "International", "level": "junior_international", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["World Juniors", "WJC"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "winter-olympics-hockey", "provider": "machina-sports/sports-skills", "sport": "hockey", "competition_name": "Winter Olympics Ice Hockey", "country_or_region": "International", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["Olympic Hockey"], "discovery_status": "unconfirmed_reference"},
    ],
    "basketball": [
        # North America
        {"competition_id": "nba", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "National Basketball Association (NBA)", "country_or_region": "USA", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["NBA"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "wnba", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Women's National Basketball Association (WNBA)", "country_or_region": "USA", "level": "tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["WNBA"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "nba-g-league", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "NBA G League", "country_or_region": "USA", "level": "tier_2", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["G League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "cbb", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "NCAA College Basketball (CBB)", "country_or_region": "USA", "level": "college", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["NCAA Basketball", "NCAA Men's Basketball", "March Madness"], "discovery_status": "unconfirmed_reference"},
        # European & Continental
        {"competition_id": "euroleague", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "EuroLeague Basketball", "country_or_region": "Europe", "level": "continental_tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["EuroLeague", "Turkish Airlines EuroLeague"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "eurocup", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "BKT EuroCup", "country_or_region": "Europe", "level": "continental_tier_2", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["EuroCup", "ULEB Eurocup"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "liga-acb", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Liga ACB", "country_or_region": "Spain", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["ACB", "Liga Endesa"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "lega-basket-serie-a", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Lega Basket Serie A (LBA)", "country_or_region": "Italy", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 1, "supported": False, "aliases": ["LBA Serie A", "Lega Basket"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "bcl", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Basketball Champions League (BCL)", "country_or_region": "Europe", "level": "continental_tier_2", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["BCL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "lnb-pro-a", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "LNB Pro A", "country_or_region": "France", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["Betclic Élite", "Pro A"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "bbl", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Basketball Bundesliga (BBL)", "country_or_region": "Germany", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["BBL", "easyCredit BBL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "greek-basket-league", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Greek Basket League", "country_or_region": "Greece", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["HEBA A1", "GBL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "turkiye-bsl", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Basketbol Süper Ligi (BSL)", "country_or_region": "Turkey", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["BSL", "Türkiye Sigorta Basketbol Süper Ligi"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "vtb-united-league", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "VTB United League", "country_or_region": "International/Eurasia", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["VTB"], "discovery_status": "unconfirmed_reference"},
        # Asia & Oceania
        {"competition_id": "cba", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Chinese Basketball Association (CBA)", "country_or_region": "China", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["CBA"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "nbl-australia", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "National Basketball League (NBL)", "country_or_region": "Australia/NZ", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["NBL", "Australian NBL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "bleague-japan", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "B.League", "country_or_region": "Japan", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["B1 League", "B.League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "kbl", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "Korean Basketball League (KBL)", "country_or_region": "South Korea", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["KBL"], "discovery_status": "unconfirmed_reference"},
        # FIBA Tournaments
        {"competition_id": "fiba-world-cup", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "FIBA Basketball World Cup", "country_or_region": "International", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["FIBA World Cup"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "fiba-eurobasket", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "FIBA EuroBasket", "country_or_region": "Europe", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["EuroBasket"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "fiba-americup", "provider": "machina-sports/sports-skills", "sport": "basketball", "competition_name": "FIBA AmeriCup", "country_or_region": "Americas", "level": "international", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["AmeriCup"], "discovery_status": "unconfirmed_reference"},
    ],
    "baseball": [
        # Major Leagues North America & Asia
        {"competition_id": "mlb", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Major League Baseball (MLB)", "country_or_region": "USA/Canada", "level": "tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["MLB"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "npb", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Nippon Professional Baseball (NPB)", "country_or_region": "Japan", "level": "tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["NPB", "Japanese Baseball"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "kbo", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "KBO League", "country_or_region": "South Korea", "level": "tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["KBO", "Korean Baseball"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "cpbl", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Chinese Professional Baseball League (CPBL)", "country_or_region": "Taiwan", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["CPBL", "Taiwan Baseball"], "discovery_status": "unconfirmed_reference"},
        # Latin America & Caribbean
        {"competition_id": "lmb", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Liga Mexicana de Béisbol (LMB)", "country_or_region": "Mexico", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["LMB", "Mexican League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "lmp", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Liga Mexicana del Pacífico (LMP)", "country_or_region": "Mexico", "level": "winter_tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["LMP", "Mexican Pacific League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "abl", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Australian Baseball League (ABL)", "country_or_region": "Australia", "level": "tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["ABL"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "lidom", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Liga de Béisbol Profesional de la República Dominicana (LIDOM)", "country_or_region": "Dominican Republic", "level": "winter_tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["LIDOM", "Dominican Winter League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "lvbp", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Liga Venezolana de Béisbol Profesional (LVBP)", "country_or_region": "Venezuela", "level": "winter_tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["LVBP", "Venezuelan Winter League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "lbprc", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Liga de Béisbol Profesional Roberto Clemente (LBPRC)", "country_or_region": "Puerto Rico", "level": "winter_tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["LBPRC", "Puerto Rico Winter League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "serie-nacional", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Serie Nacional de Béisbol (Cuba)", "country_or_region": "Cuba", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["SNB", "Serie Nacional"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "lpbc-colombia", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Liga Profesional de Béisbol Colombiano (LPBC)", "country_or_region": "Colombia", "level": "winter_tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["LPBC"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "lbpn-nicaragua", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Liga de Béisbol Profesional Nacional (LBPN)", "country_or_region": "Nicaragua", "level": "winter_tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["LBPN"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "probeis-panama", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Liga Profesional de Béisbol de Panamá (PROBEIS)", "country_or_region": "Panama", "level": "winter_tier_1", "active": False, "current_season": "2026/2027", "priority": 2, "supported": False, "aliases": ["PROBEIS"], "discovery_status": "unconfirmed_reference"},
        # European Leagues
        {"competition_id": "serie-a-baseball-italy", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Serie A Baseball", "country_or_region": "Italy", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Italian Baseball League", "Serie A Baseball"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "honkbal-hoofdklasse", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Honkbal Hoofdklasse", "country_or_region": "Netherlands", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Hoofdklasse", "Dutch Major League"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "czech-baseball-extraliga", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Czech Baseball Extraliga", "country_or_region": "Czechia", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Czech Extraliga Baseball"], "discovery_status": "unconfirmed_reference"},
        # International Tournaments
        {"competition_id": "wbc", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "World Baseball Classic (WBC)", "country_or_region": "International", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["WBC"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "wbsc-premier12", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "WBSC Premier12", "country_or_region": "International", "level": "international", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["Premier12"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "serie-del-caribe", "provider": "machina-sports/sports-skills", "sport": "baseball", "competition_name": "Serie del Caribe (Caribbean Series)", "country_or_region": "Latin America", "level": "regional_tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["Caribbean Series"], "discovery_status": "unconfirmed_reference"},
    ],
    "formula_1": [
        {"competition_id": "f1", "provider": "jolpica-f1", "sport": "formula_1", "competition_name": "FIA Formula One World Championship", "country_or_region": "International", "level": "tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["F1", "Formula 1", "Formula One"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "f2", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "FIA Formula 2 Championship", "country_or_region": "International", "level": "tier_2", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["F2", "Formula 2"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "f3", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "FIA Formula 3 Championship", "country_or_region": "International", "level": "tier_3", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["F3", "Formula 3"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "formula-e", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "ABB FIA Formula E World Championship", "country_or_region": "International", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Formula E", "FE"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "indycar", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "NTT IndyCar Series", "country_or_region": "USA", "level": "tier_1", "active": False, "current_season": "2026", "priority": 1, "supported": False, "aliases": ["IndyCar", "Indy 500"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "super-formula", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "Super Formula Championship", "country_or_region": "Japan", "level": "tier_1", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Super Formula"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "formula-regional", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "Formula Regional European Championship", "country_or_region": "Europe", "level": "tier_3", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["FRECA", "Formula Regional"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "euroformula-open", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "Euroformula Open Championship", "country_or_region": "Europe", "level": "tier_3", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Euroformula Open"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "f4-italian", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "Italian F4 Championship", "country_or_region": "Italy", "level": "junior", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Italian F4"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "f4-british", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "British F4 Championship", "country_or_region": "UK", "level": "junior", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["British F4"], "discovery_status": "unconfirmed_reference"},
        {"competition_id": "f4-spanish", "provider": "machina-sports/sports-skills", "sport": "formula_1", "competition_name": "Spanish F4 Championship", "country_or_region": "Spain", "level": "junior", "active": False, "current_season": "2026", "priority": 2, "supported": False, "aliases": ["Spanish F4"], "discovery_status": "unconfirmed_reference"},
    ],
}


class CompetitionRegistryService:
    """
    Manages competition discovery, caching, and matching across all supported sports.
    Avoids scraping competition discovery endpoints on every refresh cycle.
    Probes real provider availability without assuming every competition exists in every provider.
    """

    def __init__(self):
        self._memory_cache: Dict[str, Dict[str, Any]] = {}

    def _get_collection(self):
        try:
            return mongo_manager.db["competition_registry"]
        except Exception:
            return None

    def _get_f1_events_collection(self):
        try:
            return mongo_manager.db["f1_events_registry"]
        except Exception:
            return None

    async def get_or_discover_competitions(
        self, sport: str = "football", force: bool = False
    ) -> List[Dict[str, Any]]:
        sport_norm = sport.lower().strip()
        if sport_norm in ("ice_hockey", "nhl"):
            sport_norm = "hockey"
        elif sport_norm in ("f1", "formula1", "indycar"):
            sport_norm = "formula_1"

        cache_key = f"predictpro:competitions:{sport_norm}"
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. If not forcing, check Redis cache
        if not force:
            try:
                cached_redis = await redis_client.get_json(cache_key)
                if cached_redis and isinstance(cached_redis, list) and len(cached_redis) > 0:
                    return cached_redis
            except Exception as e:
                print(f"[CompetitionRegistry] Redis cache read notice: {e}")

            # 2. Check MongoDB for fresh records
            col = self._get_collection()
            if col is not None:
                try:
                    stored = list(col.find({"sport": sport_norm, "active": True}, {"_id": 0}).limit(200))
                    if stored:
                        latest_discovery = max(
                            [s.get("last_discovered_at", "") for s in stored if s.get("last_discovered_at")],
                            default=""
                        )
                        if latest_discovery and freshness_policy.is_fresh(latest_discovery, sport_norm, "registry"):
                            await redis_client.set_json(cache_key, stored, ex_seconds=COMPETITION_CACHE_TTL_SECONDS)
                            return stored
                except Exception as e:
                    print(f"[CompetitionRegistry] MongoDB read notice: {e}")

        # 3. Cache expired, not present, or force=True -> Discover from provider router / connectors
        discovered: List[Dict[str, Any]] = []

        if sport_norm == "football":
            discovered = await self._discover_football_competitions(now_iso)
        elif sport_norm == "basketball":
            discovered = await self._discover_basketball_competitions(now_iso)
        elif sport_norm == "baseball":
            discovered = await self._discover_baseball_competitions(now_iso)
        elif sport_norm == "hockey":
            discovered = await self._discover_hockey_competitions(now_iso)
        elif sport_norm == "formula_1":
            discovered = await self._discover_f1_events(now_iso)
        else:
            discovered = [dict(c) for c in DEFAULT_BASELINE_COMPETITIONS.get(sport_norm, [])]
            for c in discovered:
                c["last_discovered_at"] = now_iso

        # If discovery failed or returned empty: fall back to MongoDB last-known or baseline
        if not discovered:
            col = self._get_collection()
            if col is not None:
                try:
                    last_known = list(col.find({"sport": sport_norm}, {"_id": 0}).limit(200))
                    if last_known:
                        discovered = last_known
                except Exception:
                    pass

        if not discovered:
            discovered = [dict(c) for c in DEFAULT_BASELINE_COMPETITIONS.get(sport_norm, [])]
            for c in discovered:
                c["last_discovered_at"] = now_iso

        # 4. Upsert discovered competitions into MongoDB
        col = self._get_collection()
        if col is not None:
            for comp in discovered:
                try:
                    c_id = comp.get("competition_id")
                    if c_id:
                        col.update_one(
                            {"competition_id": c_id, "sport": sport_norm},
                            {"$set": comp},
                            upsert=True,
                        )
                except Exception as e:
                    print(f"[CompetitionRegistry] MongoDB upsert notice: {e}")

        # 5. Cache in Redis
        try:
            await redis_client.set_json(cache_key, discovered, ex_seconds=COMPETITION_CACHE_TTL_SECONDS)
        except Exception as e:
            print(f"[CompetitionRegistry] Redis write notice: {e}")

        return discovered

    async def _discover_football_competitions(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        try:
            from sports_skills.football._connector import LEAGUES
            for slug, league in LEAGUES.items():
                name = league.get("name", slug.replace("-", " ").title())
                country = league.get("country", "International")
                is_primary = slug in (
                    "premier-league", "la-liga", "serie-a", "bundesliga", "ligue-1",
                    "champions-league", "europa-league", "conference-league",
                    "uefa-nations-league", "world-cup", "copa-libertadores", "mls"
                )
                discovered.append({
                    "competition_id": slug,
                    "provider": "machina-sports/sports-skills",
                    "provider_competition_id": slug,
                    "sport": "football",
                    "competition_name": name,
                    "country_or_region": country,
                    "level": "tier_1" if is_primary else "tier_2",
                    "active": True,
                    "current_season": "2026/2027",
                    "priority": 1 if is_primary else 2,
                    "supported": True,
                    "last_discovered_at": now_iso,
                    "discovery_status": "active",
                })
        except Exception:
            pass

        # Keep baseline competitions strictly as unconfirmed reference catalogue
        existing_ids = {c["competition_id"] for c in discovered}
        for base in DEFAULT_BASELINE_COMPETITIONS.get("football", []):
            if base["competition_id"] not in existing_ids:
                item = dict(base)
                item["last_discovered_at"] = now_iso
                item["discovery_status"] = "unconfirmed_reference"
                item["active"] = False
                item["supported"] = False
                discovered.append(item)

        return discovered

    async def _discover_basketball_competitions(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        # Probe connector if sports_skills provides multi-league or scoreboard leagues
        try:
            import sports_skills.nba as nba_mod
            if hasattr(nba_mod, "get_competitions"):
                comps = nba_mod.get_competitions()
                for c in comps:
                    discovered.append({
                        "competition_id": str(c.get("id", "nba")).lower(),
                        "provider": "machina-sports/sports-skills",
                        "provider_competition_id": str(c.get("id", "nba")),
                        "sport": "basketball",
                        "competition_name": c.get("name", "NBA"),
                        "country_or_region": c.get("country", "USA"),
                        "level": "tier_1",
                        "active": True,
                        "current_season": "2026/2027",
                        "priority": 1,
                        "supported": True,
                        "last_discovered_at": now_iso,
                        "discovery_status": "active",
                    })
        except Exception:
            pass

        existing_ids = {c["competition_id"] for c in discovered}
        for base in DEFAULT_BASELINE_COMPETITIONS.get("basketball", []):
            if base["competition_id"] not in existing_ids:
                item = dict(base)
                item["last_discovered_at"] = now_iso
                item["discovery_status"] = "unconfirmed_reference"
                item["active"] = False
                item["supported"] = False
                discovered.append(item)

        return discovered

    async def _discover_baseball_competitions(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        try:
            import sports_skills.mlb as mlb_mod
            if hasattr(mlb_mod, "get_competitions"):
                comps = mlb_mod.get_competitions()
                for c in comps:
                    discovered.append({
                        "competition_id": str(c.get("id", "mlb")).lower(),
                        "provider": "machina-sports/sports-skills",
                        "provider_competition_id": str(c.get("id", "mlb")),
                        "sport": "baseball",
                        "competition_name": c.get("name", "Major League Baseball"),
                        "country_or_region": c.get("country", "USA"),
                        "level": "tier_1",
                        "active": True,
                        "current_season": "2026",
                        "priority": 1,
                        "supported": True,
                        "last_discovered_at": now_iso,
                        "discovery_status": "active",
                    })
        except Exception:
            pass

        existing_ids = {c["competition_id"] for c in discovered}
        for base in DEFAULT_BASELINE_COMPETITIONS.get("baseball", []):
            if base["competition_id"] not in existing_ids:
                item = dict(base)
                item["last_discovered_at"] = now_iso
                item["discovery_status"] = "unconfirmed_reference"
                item["active"] = False
                item["supported"] = False
                discovered.append(item)

        return discovered

    async def _discover_hockey_competitions(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        try:
            import sports_skills.nhl as nhl_mod
            if hasattr(nhl_mod, "get_competitions"):
                comps = nhl_mod.get_competitions()
                for c in comps:
                    discovered.append({
                        "competition_id": str(c.get("id", "nhl")).lower(),
                        "provider": "machina-sports/sports-skills",
                        "provider_competition_id": str(c.get("id", "nhl")),
                        "sport": "hockey",
                        "competition_name": c.get("name", "National Hockey League"),
                        "country_or_region": c.get("country", "USA/Canada"),
                        "level": "tier_1",
                        "active": True,
                        "current_season": "2026/2027",
                        "priority": 1,
                        "supported": True,
                        "last_discovered_at": now_iso,
                        "discovery_status": "active",
                    })
        except Exception:
            pass

        existing_ids = {c["competition_id"] for c in discovered}
        for base in DEFAULT_BASELINE_COMPETITIONS.get("hockey", []):
            if base["competition_id"] not in existing_ids:
                item = dict(base)
                item["last_discovered_at"] = now_iso
                item["discovery_status"] = "unconfirmed_reference"
                item["active"] = False
                item["supported"] = False
                discovered.append(item)

        return discovered

    async def _discover_f1_events(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        for base in DEFAULT_BASELINE_COMPETITIONS.get("formula_1", []):
            item = dict(base)
            item["last_discovered_at"] = now_iso
            item["discovery_status"] = "unconfirmed_reference"
            item["active"] = False
            item["supported"] = False
            discovered.append(item)
        return discovered

    def match_competition_name(self, league_name: str, competitions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not league_name:
            return None
        clean = league_name.lower().strip().replace("_", " ").replace("-", " ")
        
        # 1. Exact match on ID, name, or aliases
        for comp in competitions:
            c_name = comp.get("competition_name", "").lower().replace("_", " ").replace("-", " ")
            c_id = comp.get("competition_id", "").lower().replace("_", " ").replace("-", " ")
            if clean == c_name or clean == c_id:
                return comp
            for alias in comp.get("aliases", []):
                if clean == alias.lower().strip():
                    return comp

        # 2. Substring matching
        for comp in competitions:
            c_name = comp.get("competition_name", "").lower().replace("_", " ").replace("-", " ")
            c_id = comp.get("competition_id", "").lower().replace("_", " ").replace("-", " ")
            if c_name in clean or clean in c_name or c_id in clean:
                return comp
            for alias in comp.get("aliases", []):
                a_clean = alias.lower().strip()
                if a_clean in clean or clean in a_clean:
                    return comp

        return None

    def update_fixture_refresh_timestamp(self, competition_id: str, sport: str):
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        col = self._get_collection()
        if col is not None:
            try:
                col.update_one(
                    {"competition_id": competition_id, "sport": sport},
                    {"$set": {"last_fixture_refresh_at": now_iso}},
                )
            except Exception:
                pass


competition_registry_service = CompetitionRegistryService()
