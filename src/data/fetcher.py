"""
Sports data fetcher — pulls from free public APIs:
  - ESPN public API (game scores, schedules, team stats)
  - The Odds API (betting lines, requires free API key)
  - Ball Don't Lie API (NBA player stats, free)
  - NHL API (official, free)
  - pybaseball (MLB stats)
  - nfl_data_py (NFL stats)
"""
import time
import requests
from datetime import datetime, timedelta
from typing import Optional

from config import (
    ESPN_BASE, ESPN_SPORTS, BALLDONTLIE_BASE,
    ODDS_API_BASE, ODDS_API_KEY, ODDS_REGIONS, ODDS_MARKETS, ODDS_FORMAT
)


def _get(url: str, params: dict = None, retries: int = 3) -> Optional[dict]:
    """HTTP GET with retry logic."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                time.sleep(2 ** attempt)
            elif e.response.status_code >= 500:
                time.sleep(1)
            else:
                return None
        except requests.RequestException:
            time.sleep(1)
    return None


# ─── ESPN API ─────────────────────────────────────────────────────────────────

def fetch_espn_scoreboard(sport_key: str, date: str = None) -> list[dict]:
    """
    Fetch games from ESPN scoreboard.
    sport_key: one of ESPN_SPORTS keys (NBA, NFL, MLB, NHL, etc.)
    date: YYYYMMDD format, defaults to today
    """
    sport_path = ESPN_SPORTS.get(sport_key)
    if not sport_path:
        return []

    url = f"{ESPN_BASE}/{sport_path}/scoreboard"
    params = {}
    if date:
        params["dates"] = date

    data = _get(url, params)
    if not data:
        return []

    games = []
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])

        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})

        status = event.get("status", {})
        status_type = status.get("type", {})

        game_date_str = event.get("date", "")
        try:
            game_date = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
        except ValueError:
            game_date = datetime.utcnow()

        games.append({
            "id": event.get("id"),
            "sport": sport_key,
            "league": sport_path.split("/")[-1].upper(),
            "home_team": home.get("team", {}).get("displayName", ""),
            "away_team": away.get("team", {}).get("displayName", ""),
            "home_score": float(home.get("score", 0) or 0),
            "away_score": float(away.get("score", 0) or 0),
            "game_date": game_date,
            "status": status_type.get("name", "scheduled").lower(),
            "venue": comp.get("venue", {}).get("fullName", ""),
            "home_record": home.get("records", [{}])[0].get("summary", ""),
            "away_record": away.get("records", [{}])[0].get("summary", ""),
        })
    return games


def fetch_espn_team_stats(sport_key: str, season: str = None) -> list[dict]:
    """Fetch team statistics from ESPN."""
    sport_path = ESPN_SPORTS.get(sport_key)
    if not sport_path:
        return []

    url = f"{ESPN_BASE}/{sport_path}/standings"
    params = {}
    if season:
        params["season"] = season

    data = _get(url, params)
    if not data:
        return []

    teams = []
    for group in data.get("children", [data]):
        for entry in group.get("standings", {}).get("entries", []):
            team_info = entry.get("team", {})
            stats_raw = {s["name"]: s["value"] for s in entry.get("stats", [])}
            teams.append({
                "sport": sport_key,
                "team": team_info.get("displayName", ""),
                "wins": stats_raw.get("wins", 0),
                "losses": stats_raw.get("losses", 0),
                "win_pct": stats_raw.get("winPercent", 0),
                "points_for": stats_raw.get("pointsFor", stats_raw.get("runs", 0)),
                "points_against": stats_raw.get("pointsAgainst", 0),
                "home_wins": stats_raw.get("homeWins", 0),
                "home_losses": stats_raw.get("homeLosses", 0),
                "away_wins": stats_raw.get("roadWins", 0),
                "away_losses": stats_raw.get("roadLosses", 0),
                "streak": stats_raw.get("streak", 0),
                "season": season or "current",
            })
    return teams


def fetch_upcoming_games(sport_key: str, days_ahead: int = 3) -> list[dict]:
    """Fetch upcoming games for the next N days."""
    all_games = []
    for i in range(days_ahead + 1):
        date = (datetime.utcnow() + timedelta(days=i)).strftime("%Y%m%d")
        games = fetch_espn_scoreboard(sport_key, date)
        all_games.extend([g for g in games if g["status"] in ("scheduled", "pre")])
    return all_games


# ─── The Odds API ─────────────────────────────────────────────────────────────

def fetch_available_sports() -> list[dict]:
    """List all sports available on The Odds API."""
    if not ODDS_API_KEY:
        return []
    url = f"{ODDS_API_BASE}/sports"
    data = _get(url, {"apiKey": ODDS_API_KEY})
    return data or []


def fetch_game_odds(sport_key: str) -> list[dict]:
    """
    Fetch betting odds for a sport.
    sport_key: The Odds API sport key (e.g., 'basketball_nba', 'americanfootball_nfl')
    """
    if not ODDS_API_KEY:
        print("[!] ODDS_API_KEY not set. Using mock odds data.")
        return []

    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": ODDS_REGIONS,
        "markets": ODDS_MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": "iso",
    }
    data = _get(url, params)
    if not data:
        return []

    games = []
    for event in data:
        game_odds = {
            "id": event.get("id"),
            "sport": sport_key,
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
            "commence_time": event.get("commence_time"),
            "bookmakers": [],
        }
        for bm in event.get("bookmakers", []):
            bm_data = {"bookmaker": bm["title"], "markets": {}}
            for market in bm.get("markets", []):
                market_key = market["key"]
                bm_data["markets"][market_key] = {
                    o["name"]: {"price": o["price"], "point": o.get("point")}
                    for o in market.get("outcomes", [])
                }
            game_odds["bookmakers"].append(bm_data)
        games.append(game_odds)
    return games


def fetch_player_props(sport_key: str, event_ids: list[str] = None) -> list[dict]:
    """Fetch player prop odds from The Odds API."""
    if not ODDS_API_KEY:
        return []

    sport_prop_markets = {
        "basketball_nba": "player_points,player_rebounds,player_assists,player_threes",
        "americanfootball_nfl": "player_pass_yds,player_rush_yds,player_reception_yds,player_anytime_td",
        "baseball_mlb": "batter_home_runs,batter_rbis,batter_hits,pitcher_strikeouts",
        "icehockey_nhl": "player_points,player_goals,player_shots_on_goal",
    }

    markets = sport_prop_markets.get(sport_key, "")
    if not markets:
        return []

    url = f"{ODDS_API_BASE}/sports/{sport_key}/events"
    events_data = _get(url, {"apiKey": ODDS_API_KEY})
    if not events_data:
        return []

    props = []
    for event in (events_data[:5] if not event_ids else events_data):  # limit API calls
        if event_ids and event["id"] not in event_ids:
            continue

        prop_url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event['id']}/odds"
        prop_data = _get(prop_url, {
            "apiKey": ODDS_API_KEY,
            "regions": ODDS_REGIONS,
            "markets": markets,
            "oddsFormat": ODDS_FORMAT,
        })
        if not prop_data:
            continue

        for bm in prop_data.get("bookmakers", []):
            for market in bm.get("markets", []):
                prop_type = market["key"].replace("player_", "")
                for outcome in market.get("outcomes", []):
                    props.append({
                        "event_id": event["id"],
                        "sport": sport_key,
                        "home_team": event.get("home_team"),
                        "away_team": event.get("away_team"),
                        "player_name": outcome.get("description", outcome.get("name")),
                        "prop_type": prop_type,
                        "line": outcome.get("point"),
                        "side": "over" if "over" in outcome["name"].lower() else "under",
                        "odds": outcome["price"],
                        "bookmaker": bm["title"],
                    })
        time.sleep(0.2)  # respect rate limits

    return props


# ─── Ball Don't Lie API (NBA) ────────────────────────────────────────────────

def fetch_nba_players(search: str = None) -> list[dict]:
    """Fetch NBA player info from Ball Don't Lie API."""
    params = {"per_page": 100}
    if search:
        params["search"] = search
    data = _get(f"{BALLDONTLIE_BASE}/players", params)
    return data.get("data", []) if data else []


def fetch_nba_game_stats(game_id: int = None, season: int = None, player_ids: list = None) -> list[dict]:
    """Fetch NBA player game stats."""
    params = {"per_page": 100}
    if game_id:
        params["game_ids[]"] = game_id
    if season:
        params["seasons[]"] = season
    if player_ids:
        params["player_ids[]"] = player_ids[:10]  # limit

    data = _get(f"{BALLDONTLIE_BASE}/stats", params)
    return data.get("data", []) if data else []


def fetch_nba_season_averages(season: int, player_ids: list) -> list[dict]:
    """Fetch NBA season averages for players."""
    params = {"season": season}
    for pid in player_ids[:10]:
        params[f"player_ids[]"] = pid

    data = _get(f"{BALLDONTLIE_BASE}/season_averages", params)
    return data.get("data", []) if data else []


# ─── NHL API (official, free) ─────────────────────────────────────────────────

def fetch_nhl_schedule(date: str = None) -> list[dict]:
    """Fetch NHL schedule for a given date."""
    date = date or datetime.utcnow().strftime("%Y-%m-%d")
    url = f"https://api-web.nhle.com/v1/schedule/{date}"
    data = _get(url)
    if not data:
        return []

    games = []
    for game_week in data.get("gameWeek", []):
        for game in game_week.get("games", []):
            games.append({
                "id": str(game.get("id")),
                "sport": "NHL",
                "home_team": game.get("homeTeam", {}).get("placeName", {}).get("default", ""),
                "away_team": game.get("awayTeam", {}).get("placeName", {}).get("default", ""),
                "home_score": game.get("homeTeam", {}).get("score", 0),
                "away_score": game.get("awayTeam", {}).get("score", 0),
                "game_date": datetime.fromisoformat(game.get("startTimeUTC", datetime.utcnow().isoformat())),
                "status": game.get("gameState", "FUT").lower(),
                "venue": game.get("venue", {}).get("default", ""),
            })
    return games


def fetch_nhl_standings() -> list[dict]:
    """Fetch current NHL standings."""
    url = "https://api-web.nhle.com/v1/standings/now"
    data = _get(url)
    if not data:
        return []

    teams = []
    for team in data.get("standings", []):
        teams.append({
            "sport": "NHL",
            "team": team.get("teamName", {}).get("default", ""),
            "wins": team.get("wins", 0),
            "losses": team.get("losses", 0),
            "ot_losses": team.get("otLosses", 0),
            "points": team.get("points", 0),
            "goals_for": team.get("goalFor", 0),
            "goals_against": team.get("goalAgainst", 0),
            "home_wins": team.get("homeWins", 0),
            "home_losses": team.get("homeLosses", 0),
            "road_wins": team.get("roadWins", 0),
            "road_losses": team.get("roadLosses", 0),
            "streak": team.get("streakCode", ""),
            "last10_wins": team.get("l10Wins", 0),
            "last10_losses": team.get("l10Losses", 0),
        })
    return teams


# ─── NFL Data ─────────────────────────────────────────────────────────────────

def fetch_nfl_data(season: int = 2024) -> dict:
    """
    Fetch NFL data using nfl_data_py library.
    Returns schedules, team stats, and player stats.
    """
    try:
        import nfl_data_py as nfl
        schedules = nfl.import_schedules([season])
        team_desc = nfl.import_team_desc()
        player_stats = nfl.import_weekly_rosters([season])

        return {
            "schedules": schedules.to_dict("records") if schedules is not None else [],
            "teams": team_desc.to_dict("records") if team_desc is not None else [],
            "players": player_stats.to_dict("records") if player_stats is not None else [],
        }
    except ImportError:
        print("[!] nfl_data_py not installed. Run: pip install nfl-data-py")
        return {}
    except Exception as e:
        print(f"[!] NFL data fetch error: {e}")
        return {}


# ─── MLB Data ─────────────────────────────────────────────────────────────────

def fetch_mlb_data(season: int = 2024) -> dict:
    """
    Fetch MLB data using pybaseball library.
    Returns batting stats and pitching stats.
    """
    try:
        from pybaseball import batting_stats, pitching_stats, standings
        import warnings
        warnings.filterwarnings("ignore")

        batting = batting_stats(season, qual=50)
        pitching = pitching_stats(season, qual=20)

        return {
            "batting": batting.to_dict("records") if batting is not None else [],
            "pitching": pitching.to_dict("records") if pitching is not None else [],
        }
    except ImportError:
        print("[!] pybaseball not installed. Run: pip install pybaseball")
        return {}
    except Exception as e:
        print(f"[!] MLB data fetch error: {e}")
        return {}


# ─── Mock data for testing (when no API keys) ─────────────────────────────────

def get_mock_odds(sport: str, home_team: str, away_team: str) -> dict:
    """Generate realistic mock odds when Odds API is unavailable."""
    import random
    rng = random.Random(hash(f"{sport}{home_team}{away_team}"))

    # Home team moneyline (slight home advantage)
    home_ml = rng.randint(-150, -105)
    # Derive away from home (with juice)
    away_ml = int(100 / (1 + 1 / abs(home_ml)) * 100) if home_ml < 0 else rng.randint(100, 140)

    spread = round(rng.uniform(-7.5, 7.5) * 2) / 2
    total = round(rng.uniform(195, 235) if sport == "NBA"
                  else rng.uniform(40, 50) if sport == "NFL"
                  else rng.uniform(6, 10) if sport == "MLB"
                  else rng.uniform(5, 7.5), 1)

    return {
        "home_ml": home_ml,
        "away_ml": away_ml,
        "home_spread": -spread,
        "away_spread": spread,
        "spread_odds": -110,
        "total_line": total,
        "over_odds": -110,
        "under_odds": -110,
        "source": "mock",
    }
