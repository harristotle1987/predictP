# PredictPro Backend Package
import sys

try:
    import sports_skills
    import sports_skills.nba
    import sports_skills.mlb
    import sports_skills.nhl
    try:
        import sports_skills.f1
        sys.modules["sports_skills.fastf1"] = sports_skills.f1
        sports_skills.fastf1 = sports_skills.f1
    except Exception:
        pass

    sys.modules["sports_skills.nba_data"] = sports_skills.nba
    sys.modules["sports_skills.mlb_data"] = sports_skills.mlb
    sys.modules["sports_skills.nhl_data"] = sports_skills.nhl
    sports_skills.nba_data = sports_skills.nba
    sports_skills.mlb_data = sports_skills.mlb
    sports_skills.nhl_data = sports_skills.nhl
except Exception:
    pass

