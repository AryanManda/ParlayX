"""
Multi-AI advisor — gets a single best pick from Claude, ChatGPT, DeepSeek, and Gemini.
Each model analyzes the current +EV bets and returns its top recommendation.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import ANTHROPIC_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY


def _build_prompt(bets: list[dict]) -> str:
    if not bets:
        lines = ["No live bets available — give a general sports betting insight for today."]
    else:
        top = bets[:5]
        lines = ["Top +EV bets today:\n"]
        for i, b in enumerate(top, 1):
            lines.append(
                f"{i}. {b.get('description','')} | {b.get('teams','')} | {b.get('sport','')} | "
                f"Odds: {b.get('best_odds',0):+d} | Model: {b.get('model_prob',0)}% | EV: +{b.get('ev_pct',0)}%"
            )

    prompt = "\n".join(lines)
    prompt += """

You are an expert sports bettor covering NBA, NFL, MLB, NHL, and College Basketball (NCAAB). Based on the bets above, select your SINGLE best pick for today.

Respond in this exact JSON format (no markdown, just raw JSON):
{
  "pick": "Short bet description (e.g. Lakers ML -110)",
  "teams": "Team A vs Team B",
  "sport": "NBA",
  "odds": -110,
  "reasoning": "2-3 sentence explanation of why this is the best bet",
  "key_edge": "One-sentence summary of the core edge",
  "confidence": "high|medium|low",
  "verdict": "BET|WATCH|PASS",
  "risk_warning": "One sentence on the main risk to this bet"
}"""
    return prompt


def _parse_pick(raw: str, bot_name: str) -> dict:
    """Extract JSON from model response."""
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return {
        "pick": "No pick available",
        "teams": "—",
        "sport": "—",
        "odds": 0,
        "reasoning": raw[:400] if raw else f"{bot_name} did not return a valid pick.",
        "key_edge": "—",
        "confidence": "low",
        "verdict": "PASS",
        "risk_warning": "Could not parse response.",
    }


# ── Claude (Anthropic) ────────────────────────────────────────────────────────

def get_claude_pick(bets: list[dict]) -> dict:
    if not ANTHROPIC_API_KEY:
        return _no_key_response("Claude", "ANTHROPIC_API_KEY")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=25.0)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": _build_prompt(bets)}],
        )
        raw = msg.content[0].text if msg.content else ""
        result = _parse_pick(raw, "Claude")
        result["bot"] = "Claude"
        result["model"] = "claude-haiku-4-5-20251001"
        return result
    except Exception as e:
        err = str(e)
        if "credit" in err.lower() or "billing" in err.lower():
            return {**_no_key_response("Claude", "ANTHROPIC_API_KEY"),
                    "reasoning": "Your Anthropic account has no credits. Add credits at console.anthropic.com → Billing.",
                    "verdict": "UNAVAILABLE", "error": "No credits"}
        return _error_response("Claude", err)


# ── ChatGPT (OpenAI) ──────────────────────────────────────────────────────────

def get_chatgpt_pick(bets: list[dict]) -> dict:
    if not OPENAI_API_KEY:
        return _no_key_response("ChatGPT", "OPENAI_API_KEY")
    try:
        import openai
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=600,
            messages=[
                {"role": "system", "content": "You are an expert sports betting analyst. Always respond with valid JSON only."},
                {"role": "user", "content": _build_prompt(bets)},
            ],
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        result = _parse_pick(raw, "ChatGPT")
        result["bot"] = "ChatGPT"
        result["model"] = "gpt-4o"
        return result
    except Exception as e:
        return _error_response("ChatGPT", str(e))


# ── DeepSeek ──────────────────────────────────────────────────────────────────

def get_deepseek_pick(bets: list[dict]) -> dict:
    if not DEEPSEEK_API_KEY:
        return _no_key_response("DeepSeek", "DEEPSEEK_API_KEY")
    try:
        import openai
        client = openai.OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )
        resp = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=600,
            messages=[
                {"role": "system", "content": "You are an expert sports betting analyst. Always respond with valid JSON only."},
                {"role": "user", "content": _build_prompt(bets)},
            ],
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        result = _parse_pick(raw, "DeepSeek")
        result["bot"] = "DeepSeek"
        result["model"] = "deepseek-chat"
        return result
    except Exception as e:
        return _error_response("DeepSeek", str(e))


# ── Gemini (Google) ───────────────────────────────────────────────────────────

def get_gemini_pick(bets: list[dict]) -> dict:
    if not GEMINI_API_KEY:
        return _no_key_response("Gemini", "GEMINI_API_KEY")
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(
            _build_prompt(bets),
            generation_config={"max_output_tokens": 600},
        )
        raw = resp.text if hasattr(resp, "text") else ""
        result = _parse_pick(raw, "Gemini")
        result["bot"] = "Gemini"
        result["model"] = "gemini-1.5-flash"
        return result
    except Exception as e:
        return _error_response("Gemini", str(e))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _no_key_response(bot: str, key_name: str) -> dict:
    return {
        "bot": bot,
        "model": "—",
        "pick": "API key not configured",
        "teams": "—",
        "sport": "—",
        "odds": 0,
        "reasoning": f"Add {key_name} to your .env file to enable {bot} analysis.",
        "key_edge": "—",
        "confidence": "low",
        "verdict": "UNAVAILABLE",
        "risk_warning": f"Set {key_name} in .env to activate.",
        "error": f"Missing {key_name}",
    }


def _error_response(bot: str, error: str) -> dict:
    return {
        "bot": bot,
        "model": "—",
        "pick": "Error fetching pick",
        "teams": "—",
        "sport": "—",
        "odds": 0,
        "reasoning": f"Error calling {bot} API: {error[:200]}",
        "key_edge": "—",
        "confidence": "low",
        "verdict": "UNAVAILABLE",
        "risk_warning": "API error occurred.",
        "error": error[:200],
    }


# ── Final Four Rankings ───────────────────────────────────────────────────────

def _build_final_four_prompt(teams: list[str]) -> str:
    if teams and len(teams) >= 2:
        teams_str = ", ".join(teams)
        intro = f"The NCAA Men's Basketball Final Four teams are: {teams_str}."
    else:
        intro = "Identify and analyze the current NCAA Men's Basketball Final Four teams (April 2026)."

    return f"""{intro}

Rank all 4 Final Four teams by their probability of winning the NCAA Championship.

Respond in this EXACT JSON format (no markdown, no extra text, just raw JSON):
{{
  "rankings": [
    {{
      "rank": 1,
      "team": "Team Name",
      "win_prob": 42,
      "seed": 1,
      "region": "East",
      "record": "30-5",
      "key_strength": "One sentence about biggest advantage",
      "key_risk": "One sentence about biggest risk"
    }},
    {{
      "rank": 2,
      "team": "Team Name",
      "win_prob": 28,
      "seed": 2,
      "region": "South",
      "record": "29-6",
      "key_strength": "One sentence",
      "key_risk": "One sentence"
    }},
    {{
      "rank": 3,
      "team": "Team Name",
      "win_prob": 18,
      "seed": 3,
      "region": "West",
      "record": "28-7",
      "key_strength": "One sentence",
      "key_risk": "One sentence"
    }},
    {{
      "rank": 4,
      "team": "Team Name",
      "win_prob": 12,
      "seed": 4,
      "region": "Midwest",
      "record": "27-8",
      "key_strength": "One sentence",
      "key_risk": "One sentence"
    }}
  ],
  "analysis": "2-3 sentence overall championship analysis and prediction",
  "dark_horse": "Team most likely to exceed expectations",
  "championship_game": "Team A vs Team B"
}}"""


def _parse_ff_ranking(raw: str, bot_name: str) -> dict:
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw[start:end])
            data["bot"] = bot_name
            return data
    except Exception:
        pass
    return _error_ff_response(bot_name, f"Could not parse response: {raw[:200]}")


def _no_key_ff_response(bot: str) -> dict:
    return {"bot": bot, "rankings": [], "analysis": f"API key not configured for {bot}.",
            "dark_horse": "—", "championship_game": "—", "error": "Missing API key"}


def _error_ff_response(bot: str, error: str) -> dict:
    return {"bot": bot, "rankings": [], "analysis": f"Error: {error[:200]}",
            "dark_horse": "—", "championship_game": "—", "error": error[:200]}


def get_claude_final_four(teams: list[str]) -> dict:
    if not ANTHROPIC_API_KEY:
        return _no_key_ff_response("Claude")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=30.0)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": _build_final_four_prompt(teams)}],
        )
        raw = msg.content[0].text if msg.content else ""
        result = _parse_ff_ranking(raw, "Claude")
        result["model"] = "claude-haiku-4-5-20251001"
        return result
    except Exception as e:
        err = str(e)
        if "credit" in err.lower() or "billing" in err.lower():
            r = _no_key_ff_response("Claude")
            r["analysis"] = "Your Anthropic account has no credits. Add credits at console.anthropic.com → Billing."
            return r
        return _error_ff_response("Claude", err)


def get_chatgpt_final_four(teams: list[str]) -> dict:
    if not OPENAI_API_KEY:
        return _no_key_ff_response("ChatGPT")
    try:
        import openai
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=900,
            messages=[
                {"role": "system", "content": "You are an expert college basketball analyst. Always respond with valid JSON only."},
                {"role": "user", "content": _build_final_four_prompt(teams)},
            ],
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        result = _parse_ff_ranking(raw, "ChatGPT")
        result["model"] = "gpt-4o"
        return result
    except Exception as e:
        return _error_ff_response("ChatGPT", str(e))


def get_deepseek_final_four(teams: list[str]) -> dict:
    if not DEEPSEEK_API_KEY:
        return _no_key_ff_response("DeepSeek")
    try:
        import openai
        client = openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=900,
            messages=[
                {"role": "system", "content": "You are an expert college basketball analyst. Always respond with valid JSON only."},
                {"role": "user", "content": _build_final_four_prompt(teams)},
            ],
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        result = _parse_ff_ranking(raw, "DeepSeek")
        result["model"] = "deepseek-chat"
        return result
    except Exception as e:
        return _error_ff_response("DeepSeek", str(e))


def get_gemini_final_four(teams: list[str]) -> dict:
    if not GEMINI_API_KEY:
        return _no_key_ff_response("Gemini")
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(
            _build_final_four_prompt(teams),
            generation_config={"max_output_tokens": 900},
        )
        raw = resp.text if hasattr(resp, "text") else ""
        result = _parse_ff_ranking(raw, "Gemini")
        result["model"] = "gemini-1.5-flash"
        return result
    except Exception as e:
        return _error_ff_response("Gemini", str(e))


def get_all_picks(bets: list[dict]) -> dict:
    """Run all 4 AI advisors in parallel threads and return their picks."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tasks = {
        "claude": get_claude_pick,
        "chatgpt": get_chatgpt_pick,
        "deepseek": get_deepseek_pick,
        "gemini": get_gemini_pick,
    }

    results = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fn, bets): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = _error_response(name.capitalize(), str(e))

    return results
