"""
ParlayX FastAPI backend — serves the web dashboard and REST API.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from src.data.storage import init_db
from src.analysis.ev_calculator import evaluate_bet, screen_bets, BetEvaluation
from src.analysis.correlation import suggest_uncorrelated_combo
from src.parlay.builder import build_parlay, find_best_parlays
from src.engine import analyze_game, scan_today, get_elo
from src.ai.advisor import get_advisor
from config import ANTHROPIC_API_KEY, ODDS_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY

app = FastAPI(title="ParlayX API", version="1.0")

# Mount static files
STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── In-memory state ────────────────────────────────────────────────────────────
_state = {
    "last_scan": None,
    "ev_bets": [],
    "parlays": [],
    "scan_running": False,
    "scan_sports": [],
    "scan_progress": "",
}


# ── Pydantic models ────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    sports: list[str] = ["NBA", "NFL", "MLB", "NHL"]
    bankroll: float = 1000.0
    legs: int = 3
    ai_analysis: bool = True

class CustomLeg(BaseModel):
    sport: str
    description: str
    odds: int
    model_prob: float
    bet_type: str = "moneyline"
    teams: str = ""
    game_id: str = "custom"

class CustomParlayRequest(BaseModel):
    legs: list[CustomLeg]
    bankroll: float = 1000.0
    ai_analysis: bool = True

class QuickParlayRequest(BaseModel):
    n_legs: int = 3
    bankroll: float = 1000.0


# ── Serializers ────────────────────────────────────────────────────────────────

def bet_to_dict(b: BetEvaluation) -> dict:
    return {
        "sport": b.sport,
        "description": b.description,
        "bet_type": b.bet_type,
        "outcome": b.outcome,
        "teams": b.teams,
        "game_date": b.game_date,
        "best_odds": int(b.best_odds),
        "decimal_odds": round(b.decimal_odds, 3),
        "implied_prob": round(b.implied_prob * 100, 1),
        "model_prob": round(b.model_prob * 100, 1),
        "ev_pct": round(b.ev_pct, 2),
        "edge": round(b.edge * 100, 2),
        "grade": b.grade,
        "confidence": b.model_confidence,
        "sharp_money": b.sharp_money,
        "kelly_pct": round(b.kelly_pct * 100, 2),
        "recommended_pct": round(b.recommended_pct * 100, 2),
        "bookmaker": b.bookmaker,
        "is_positive_ev": b.is_positive_ev,
    }

def parlay_to_dict(slip) -> dict:
    return {
        "grade": slip.grade,
        "composite_score": round(slip.composite_score, 1),
        "combined_american": int(slip.combined_american),
        "combined_decimal": round(slip.combined_decimal, 3),
        "model_win_prob": round(slip.model_win_prob * 100, 1),
        "simulated_win_prob": round(slip.simulated_win_prob * 100, 1),
        "ev_pct": round(slip.ev_pct, 2),
        "correlation_score": round(slip.correlation_score, 3),
        "recommended_stake_pct": round(slip.recommended_stake_pct * 100, 2),
        "recommended_stake_dollars": round(slip.recommended_stake_dollars, 2),
        "ai_analysis": slip.ai_analysis,
        "ai_confidence": slip.ai_confidence,
        "legs": [bet_to_dict(l) for l in slip.legs],
        "sim_percentile_5": round(slip.sim.percentile_5 * 100, 1),
        "sim_percentile_95": round(slip.sim.percentile_95 * 100, 1),
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/api/status")
async def get_status():
    return {
        "scan_running": _state["scan_running"],
        "last_scan": _state["last_scan"],
        "ev_bets_count": len(_state["ev_bets"]),
        "parlays_count": len(_state["parlays"]),
        "scan_progress": _state["scan_progress"],
        "has_anthropic_key": bool(ANTHROPIC_API_KEY),
        "has_odds_key": bool(ODDS_API_KEY),
        "has_openai_key": bool(OPENAI_API_KEY),
        "has_deepseek_key": bool(DEEPSEEK_API_KEY),
        "has_gemini_key": bool(GEMINI_API_KEY),
        "sports_scanned": _state["scan_sports"],
    }

@app.get("/api/bets")
async def get_bets(sport: str = None, min_ev: float = 0, min_grade: str = None):
    bets = _state["ev_bets"]
    if sport:
        bets = [b for b in bets if b["sport"] == sport.upper()]
    if min_ev:
        bets = [b for b in bets if b["ev_pct"] >= min_ev]
    grade_order = {"A+": 6, "A": 5, "B+": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    if min_grade:
        threshold = grade_order.get(min_grade, 0)
        bets = [b for b in bets if grade_order.get(b["grade"], 0) >= threshold]
    return {"bets": bets, "count": len(bets)}

@app.get("/api/parlays")
async def get_parlays():
    return {"parlays": _state["parlays"], "count": len(_state["parlays"])}

@app.post("/api/scan")
async def run_scan(req: ScanRequest, background_tasks: BackgroundTasks):
    if _state["scan_running"]:
        raise HTTPException(409, "Scan already running")
    background_tasks.add_task(_do_scan, req)
    return {"message": "Scan started", "sports": req.sports}

async def _do_scan(req: ScanRequest):
    _state["scan_running"] = True
    _state["scan_progress"] = "Starting scan..."
    _state["scan_sports"] = req.sports

    def _progress(msg: str):
        _state["scan_progress"] = msg

    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: scan_today(
            sports=req.sports,
            ai_analysis=req.ai_analysis,
            bankroll=req.bankroll,
            n_parlay_legs=req.legs,
            progress_cb=_progress,
        ))
        _state["ev_bets"] = [bet_to_dict(b) for b in results["top_ev_bets"]]
        _state["parlays"] = [parlay_to_dict(p) for p in results["best_parlays"]]
        _state["last_scan"] = datetime.now().isoformat()
        _state["scan_progress"] = f"Done — {results['ev_bets_found']} +EV bets found"
    except Exception as e:
        _state["scan_progress"] = f"Error: {str(e)}"
    finally:
        _state["scan_running"] = False

@app.post("/api/parlay/quick")
async def quick_parlay_endpoint(req: QuickParlayRequest):
    ev_bets_raw = _state.get("_raw_bets", [])
    if not ev_bets_raw:
        # Build demo bets
        ev_bets_raw = _get_demo_ev_bets()

    legs = suggest_uncorrelated_combo(ev_bets_raw, n_legs=req.n_legs)
    if not legs:
        raise HTTPException(400, "Not enough +EV bets to build parlay")

    slip = build_parlay(legs, bankroll=req.bankroll)
    return parlay_to_dict(slip)

@app.post("/api/parlay/custom")
async def custom_parlay(req: CustomParlayRequest):
    legs = []
    for l in req.legs:
        ev = evaluate_bet(
            sport=l.sport, game_id=l.game_id, bet_type=l.bet_type,
            outcome="custom", description=l.description,
            model_prob=l.model_prob / 100,
            model_confidence="medium",
            american_odds=float(l.odds),
            teams=l.teams,
        )
        legs.append(ev)

    if not legs:
        raise HTTPException(400, "No valid legs provided")

    slip = build_parlay(legs, bankroll=req.bankroll)

    if req.ai_analysis and ANTHROPIC_API_KEY:
        advisor = get_advisor()
        ai = advisor.analyze_parlay(slip)
        slip.ai_analysis = ai.get("analysis", "")
        slip.ai_confidence = ai.get("ai_confidence", "")

    return parlay_to_dict(slip)

@app.post("/api/analyze/{bet_index}")
async def analyze_bet(bet_index: int):
    bets = _state["ev_bets"]
    if bet_index >= len(bets):
        raise HTTPException(404, "Bet not found")
    if not ANTHROPIC_API_KEY:
        return {"analysis": "Set ANTHROPIC_API_KEY in .env to enable AI analysis.", "verdict": "UNAVAILABLE"}

    b = bets[bet_index]
    ev = evaluate_bet(
        sport=b["sport"], game_id="scan", bet_type=b["bet_type"],
        outcome=b["outcome"], description=b["description"],
        model_prob=b["model_prob"] / 100, model_confidence=b["confidence"],
        american_odds=float(b["best_odds"]), teams=b["teams"],
    )
    advisor = get_advisor()
    result = advisor.analyze_bet(ev)
    return result

@app.get("/api/demo")
async def run_demo():
    """Load demo data instantly without hitting any APIs."""
    from src.analysis.ev_calculator import screen_bets as _screen
    demo_raw = _get_demo_ev_bets()
    ev_bets = _screen(demo_raw)
    legs = suggest_uncorrelated_combo(ev_bets, n_legs=3)
    slip = build_parlay(legs, bankroll=1000.0)

    _state["ev_bets"] = [bet_to_dict(b) for b in ev_bets]
    _state["parlays"] = [parlay_to_dict(slip)]
    _state["last_scan"] = datetime.now().isoformat()
    _state["scan_progress"] = "Demo loaded — showing synthetic data"
    _state["scan_sports"] = ["NBA", "NFL", "MLB", "NHL"]

    return {"message": "Demo loaded", "bets": len(ev_bets), "parlays": 1}

def _get_demo_ev_bets() -> list[BetEvaluation]:
    demo = [
        evaluate_bet("NBA","g1","moneyline","home_win","Lakers ML",0.62,"high",-135,teams="Lakers vs Celtics",game_date="Today"),
        evaluate_bet("NFL","g2","moneyline","home_win","Chiefs ML",0.65,"high",-155,teams="Chiefs vs Bills",game_date="Today"),
        evaluate_bet("NBA","g3","total","over","Over 227.5",0.58,"medium",-108,teams="Nuggets vs Warriors",game_date="Today"),
        evaluate_bet("MLB","g4","moneyline","away_win","Dodgers ML",0.61,"medium",105,teams="Braves vs Dodgers",game_date="Today"),
        evaluate_bet("NHL","g5","moneyline","home_win","Avalanche ML",0.59,"medium",-120,teams="Avalanche vs Stars",game_date="Today"),
        evaluate_bet("NBA","g6","spread","home_cover","Celtics -4.5",0.56,"medium",-110,teams="Celtics vs Heat",game_date="Today"),
        evaluate_bet("NFL","g7","total","over","Over 48.5",0.57,"medium",-115,teams="Eagles vs Cowboys",game_date="Today"),
        evaluate_bet("MLB","g8","moneyline","home_win","Yankees ML",0.60,"high",-140,teams="Yankees vs Red Sox",game_date="Today"),
    ]
    return demo

@app.get("/api/ai-picks")
async def get_ai_picks():
    """Run all 4 AI models and return each one's single best pick."""
    from src.ai.multi_advisor import get_all_picks
    bets = _state.get("ev_bets", [])
    loop = asyncio.get_event_loop()
    picks = await loop.run_in_executor(None, lambda: get_all_picks(bets))
    return picks


@app.on_event("startup")
async def startup():
    init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
