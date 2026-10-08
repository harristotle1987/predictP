from typing import List, Dict, Any, Optional

SPORTS = ["football", "basketball", "baseball", "hockey", "formula_1"]

def get_candidate_percentage(item: Dict[str, Any]) -> float:
    prob = 0.0
    highest = item.get("highestPercentagePrediction")
    if isinstance(highest, dict) and highest.get("percentage") is not None:
        prob = float(highest["percentage"])
    elif item.get("calibratedPercentage") is not None:
        prob = float(item["calibratedPercentage"])
    elif item.get("percentage") is not None:
        prob = float(item["percentage"])
    elif item.get("probability") is not None:
        p = float(item["probability"])
        prob = p * 100.0 if p <= 1.0 else p
    return prob

def normalize_sport_name(sport: Optional[str]) -> str:
    s = (sport or "").lower().strip()
    if s in ("ice_hockey", "nhl"):
        return "hockey"
    if s in ("f1", "formula1"):
        return "formula_1"
    if s in SPORTS:
        return s
    return "football"

def rank_and_select_best_of_day(
    fixtures: List[Dict[str, Any]],
    max_limit: int = 20,
    max_results: Optional[int] = None,
    **kwargs,
) -> List[Dict[str, Any]]:
    """
    Publish a balanced cross-sport competition-aware Top 20.
    Workflow:
      eligible predictions -> rank within sport & competition
      -> ensure active sports/competitions are represented
      -> rank remaining candidates (with per-sport dominance guard)
      -> maximum 20.

    A sport/competition only appears when it has real fixtures + sufficient history + validated model output.
    Prevents one sport such as Hockey from monopolizing the Home feed simply because it has more qualifying games.
    """
    limit = min(20, max_results if max_results is not None else max_limit)

    # 1. Prepare highestPercentagePrediction on all candidates
    for fix in fixtures:
        markets = fix.get("markets") or fix.get("validatedMarkets") or []
        if markets and isinstance(markets, list):
            dict_markets = [m for m in markets if isinstance(m, dict)]
            if dict_markets:
                sorted_m = sorted(
                    dict_markets,
                    key=lambda m: (
                        float(m.get("calibratedPercentage") or m.get("probabilityPercentage") or 0.0),
                        float(m.get("confidenceScore") or 0.0),
                    ),
                    reverse=True,
                )
                top_m = sorted_m[0]
                fix["highestPercentagePrediction"] = {
                    "marketName": top_m.get("marketName") or fix.get("market", ""),
                    "selection": top_m.get("selection") or fix.get("selection", ""),
                    "percentage": float(top_m.get("calibratedPercentage") or top_m.get("probabilityPercentage") or 0.0),
                }

    # 2. Filter strictly to eligible candidates:
    # Step 4 Requirement: The final ranking must use ONLY:
    # validationStatus == "validated" and published == true
    # and NEVER: markets exist -> assume validated.
    validated = [
        f for f in fixtures
        if str(f.get("validationStatus") or f.get("validation_status") or "").lower().strip() == "validated"
        and f.get("published", True) is not False
        and f.get("stopReason") in (None, "", "NONE", "VALIDATED", "PUBLISHED")
        and (len(f.get("validatedMarkets") or []) > 0 or len(f.get("markets") or []) > 0)
    ]

    if not validated:
        return []

    # 3. Group by sport and competition
    # sport -> comp_key -> list of candidates
    by_sport_and_comp: Dict[str, Dict[str, List[Dict[str, Any]]]] = {s: {} for s in SPORTS}

    for candidate in validated:
        sport = normalize_sport_name(candidate.get("sport"))
        comp_key = str(candidate.get("competition_name") or candidate.get("league") or candidate.get("competition_id") or "General").strip()
        if comp_key not in by_sport_and_comp[sport]:
            by_sport_and_comp[sport][comp_key] = []
        by_sport_and_comp[sport][comp_key].append(candidate)

    # 4. Rank candidates within each sport and competition
    for sport in SPORTS:
        for comp_key in by_sport_and_comp[sport]:
            by_sport_and_comp[sport][comp_key].sort(key=get_candidate_percentage, reverse=True)

    selected: List[Dict[str, Any]] = []
    selected_ids = set()
    MAX_SPORT_SHARE = 9  # Out of 20, max 9 for one sport if other sports have available candidates
    sport_counts: Dict[str, int] = {s: 0 for s in SPORTS}

    def other_sports_have_remaining(current_sport: str) -> bool:
        for s in active_sports:
            if s != current_sport:
                for comp_cands in by_sport_and_comp[s].values():
                    if any((c.get("id") or c.get("fixtureId") or c.get("fixture_id")) not in selected_ids for c in comp_cands):
                        return True
        return False

    def add_candidate(item: Dict[str, Any], enforce_cap: bool = True) -> bool:
        c_id = item.get("id") or item.get("fixtureId") or item.get("fixture_id")
        if not c_id or c_id in selected_ids or len(selected) >= limit:
            return False
        sp = normalize_sport_name(item.get("sport"))
        if enforce_cap and sport_counts[sp] >= MAX_SPORT_SHARE and other_sports_have_remaining(sp):
            return False
        selected.append(item)
        selected_ids.add(c_id)
        sport_counts[sp] = sport_counts.get(sp, 0) + 1
        return True

    # Step A: Ensure every active sport is represented (Top 1 prediction from its best competition)
    active_sports = [s for s in SPORTS if any(len(c_list) > 0 for c_list in by_sport_and_comp[s].values())]

    for sport in active_sports:
        comp_dict = by_sport_and_comp[sport]
        # Find competition with the highest top-prediction in this sport
        best_comp_key = max(
            comp_dict.keys(),
            key=lambda ck: get_candidate_percentage(comp_dict[ck][0]) if comp_dict[ck] else -1.0,
        )
        if comp_dict[best_comp_key]:
            add_candidate(comp_dict[best_comp_key][0], enforce_cap=False)

    # Step B: Ensure active competitions within active sports are represented
    # Round-robin through active competitions not yet represented in `selected`
    for sport in active_sports:
        comp_dict = by_sport_and_comp[sport]
        for comp_key, comp_candidates in comp_dict.items():
            if len(selected) >= limit:
                break
            for cand in comp_candidates:
                if add_candidate(cand, enforce_cap=True):
                    break

    # Step C: Balanced filling of remaining slots up to limit (20)
    remaining: List[Dict[str, Any]] = []
    for sport in active_sports:
        for comp_candidates in by_sport_and_comp[sport].values():
            for cand in comp_candidates:
                c_id = cand.get("id") or cand.get("fixtureId") or cand.get("fixture_id")
                if c_id not in selected_ids:
                    remaining.append(cand)

    remaining.sort(key=get_candidate_percentage, reverse=True)

    # Pass 1: Add remaining respecting sport share cap
    postponed_candidates: List[Dict[str, Any]] = []
    for cand in remaining:
        if len(selected) >= limit:
            break
        sp = normalize_sport_name(cand.get("sport"))
        if other_sports_have_remaining(sp) and sport_counts[sp] >= MAX_SPORT_SHARE:
            postponed_candidates.append(cand)
            continue
        add_candidate(cand, enforce_cap=True)

    # Pass 2: If slots still remain (e.g. other sports have no more games), fill from postponed
    for cand in postponed_candidates:
        if len(selected) >= limit:
            break
        add_candidate(cand, enforce_cap=False)

    result = selected[:limit]

    # Assign isBestOfDay to the candidate with the highest calibrated probability
    if result:
        best_candidate = max(result, key=get_candidate_percentage)
        for f in result:
            f["isBestOfDay"] = False
        best_candidate["isBestOfDay"] = True

    return result


def build_competition_telemetry(
    discovered_fixtures: List[Dict[str, Any]],
    all_results: List[Dict[str, Any]],
    published_feed: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Builds structured cross-sport competition telemetry:
    "Sport | Competition | Fixtures discovered | Eligible | Rejected | Published"
    """
    # Group keys: (sport, competition_name)
    stats: Dict[tuple, Dict[str, int]] = {}

    def get_comp_name(fix: Dict[str, Any]) -> str:
        return str(fix.get("competition_name") or fix.get("league") or fix.get("competition_id") or "General").strip()

    def get_sport_name(fix: Dict[str, Any]) -> str:
        return normalize_sport_name(fix.get("sport"))

    # 1. Discovered fixtures
    for fix in discovered_fixtures:
        s = get_sport_name(fix)
        c = get_comp_name(fix)
        key = (s, c)
        if key not in stats:
            stats[key] = {"fixtures_discovered": 0, "eligible": 0, "rejected": 0, "published": 0}
        stats[key]["fixtures_discovered"] += 1

    # 2. Execution / evaluation results
    for r in all_results:
        s = get_sport_name(r)
        c = get_comp_name(r)
        key = (s, c)
        if key not in stats:
            stats[key] = {"fixtures_discovered": 0, "eligible": 0, "rejected": 0, "published": 0}
        
        status = str(r.get("validationStatus") or r.get("validation_status") or "").lower().strip()
        stop = str(r.get("stopReason") or "").lower().strip()
        is_rejected = stop in ("rejected_invalid", "rejected_past", "rejected_completed", "invalid_fixture", "insufficient_history") or status in ("rejected", "abstained", "insufficient_data")
        
        if is_rejected:
            stats[key]["rejected"] += 1
        else:
            stats[key]["eligible"] += 1

    # 3. Published feed
    for p in published_feed:
        s = get_sport_name(p)
        c = get_comp_name(p)
        key = (s, c)
        if key not in stats:
            stats[key] = {"fixtures_discovered": 0, "eligible": 0, "rejected": 0, "published": 0}
        stats[key]["published"] += 1

    telemetry_rows: List[Dict[str, Any]] = []
    # Sort by sport order, then competition name
    sorted_keys = sorted(stats.keys(), key=lambda k: (SPORTS.index(k[0]) if k[0] in SPORTS else 99, k[1]))

    for s, c in sorted_keys:
        row_stats = stats[(s, c)]
        disc = max(row_stats["fixtures_discovered"], row_stats["eligible"] + row_stats["rejected"])
        telemetry_rows.append({
            "sport": s,
            "competition": c,
            "fixtures_discovered": disc,
            "eligible": row_stats["eligible"],
            "rejected": row_stats["rejected"],
            "published": row_stats["published"],
        })

    return telemetry_rows


def format_competition_telemetry_table(telemetry_rows: List[Dict[str, Any]]) -> str:
    """
    Renders telemetry rows into an ASCII table:
    Sport | Competition | Fixtures discovered | Eligible | Rejected | Published
    """
    if not telemetry_rows:
        return "Sport | Competition | Fixtures discovered | Eligible | Rejected | Published\n(No competitions active)"

    lines = ["Sport | Competition | Fixtures discovered | Eligible | Rejected | Published"]
    for r in telemetry_rows:
        line = (
            f"{r['sport']} | {r['competition']} | "
            f"{r['fixtures_discovered']} | {r['eligible']} | "
            f"{r['rejected']} | {r['published']}"
        )
        lines.append(line)
    return "\n".join(lines)
