"""
Claude AI Advisor — cross-references parlay picks with Claude Opus 4.6.
Uses adaptive thinking for deep analysis of parlay legs, correlations,
and market inefficiencies. This is the "second opinion" layer.
"""
import json
from datetime import datetime
from typing import Optional
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.analysis.ev_calculator import BetEvaluation
from src.parlay.builder import ParlaySlip


def _build_parlay_prompt(slip: ParlaySlip, context: dict = None) -> str:
    """Build a detailed prompt for Claude to analyze a parlay."""
    legs_text = []
    for i, leg in enumerate(slip.legs, 1):
        legs_text.append(
            f"  Leg {i}: {leg.description}\n"
            f"    Sport: {leg.sport} | Bet Type: {leg.bet_type}\n"
            f"    Best Odds: {int(leg.best_odds):+d} (implied: {leg.implied_prob:.1%})\n"
            f"    Model Probability: {leg.model_prob:.1%} | Confidence: {leg.model_confidence}\n"
            f"    Expected Value: {leg.ev_pct:+.1f}% | Grade: {leg.grade}\n"
            f"    Sharp Money Signal: {'YES ⚡' if leg.sharp_money else 'No'}\n"
            f"    Teams: {leg.teams}"
        )

    ctx_text = ""
    if context:
        ctx_parts = []
        for k, v in context.items():
            ctx_parts.append(f"  {k}: {v}")
        ctx_text = "\nAdditional Context:\n" + "\n".join(ctx_parts)

    return f"""You are an expert sports betting analyst with deep knowledge of:
- Statistical modeling and expected value in sports betting
- Correlation analysis in parlay construction
- Line movement and sharp money indicators
- Sport-specific factors (injuries, pace, matchup advantages)
- Bankroll management using Kelly Criterion

Please analyze this parlay slip and provide a professional assessment.

═══════════════════════════════════════
PARLAY SLIP — {datetime.now().strftime('%Y-%m-%d')}
═══════════════════════════════════════
Grade: {slip.grade} | Composite Score: {slip.composite_score:.0f}/100
Legs: {len(slip.legs)} | Payout: {slip.combined_american:+.0f}

Simulated Win Probability: {slip.simulated_win_prob:.1%} (Monte Carlo, 100K sims)
Model Win Probability: {slip.model_win_prob:.1%} (naive, uncorrelated)
Expected Value: {slip.ev_pct:+.1f}%
Leg Correlation Score: {slip.correlation_score:.3f} (lower = more independent)
Recommended Stake: {slip.recommended_stake_pct:.2%} of bankroll

LEGS:
{"".join(legs_text)}
{ctx_text}

═══════════════════════════════════════

Please provide:

1. **OVERALL ASSESSMENT** (2-3 sentences): Is this a strong parlay? What's the key risk factor?

2. **LEG-BY-LEG ANALYSIS**: For each leg, evaluate:
   - Does the model's edge seem real or inflated?
   - Any situational factors the model might be missing? (injuries, revenge games, weather, etc.)
   - Is this a well-known trap bet or a genuine inefficiency?

3. **CORRELATION CHECK**: Are any legs secretly correlated that the model may have missed?
   (e.g., same-game parlays, teams on same bye week, QB + WR props)

4. **MARKET INTELLIGENCE**: Based on line movement and sharp money signals, what does the market suggest?

5. **VERDICT**:
   - STRONG PLAY / MODERATE PLAY / PASS / AVOID
   - Adjusted win probability estimate (your assessment vs model's)
   - Stake recommendation (increase / maintain / decrease vs {slip.recommended_stake_pct:.2%})

6. **ALTERNATIVE SUGGESTION** (optional): If you'd modify the parlay, which leg would you swap and why?

Be concise but specific. Back every claim with reasoning."""


def _build_single_bet_prompt(bet: BetEvaluation) -> str:
    """Build a prompt for analyzing a single bet."""
    return f"""You are an expert sports betting analyst. Evaluate this +EV bet opportunity:

BET: {bet.description}
Sport: {bet.sport} | Type: {bet.bet_type}
Teams: {bet.teams}
Odds: {int(bet.best_odds):+d} | Implied Probability: {bet.implied_prob:.1%}
Model Probability: {bet.model_prob:.1%} | Confidence: {bet.model_confidence}
Expected Value: {bet.ev_pct:+.1f}% | Edge: {bet.edge:+.1%}
Grade: {bet.grade} | Sharp Money: {'YES' if bet.sharp_money else 'No'}
Line Movement: {bet.line_movement:+.3f}

Key model features:
{json.dumps(bet.features, indent=2)}

In 150 words or less:
1. Is this edge real or a model artifact?
2. Any situational factors to consider?
3. VERDICT: BET / PASS / FADE — and why.
"""


class AIAdvisor:
    """
    Claude-powered betting advisor.
    Provides deep analysis of parlay slips and individual bets
    using adaptive thinking for nuanced reasoning.
    """

    def __init__(self, api_key: str = None):
        self.client = anthropic.Anthropic(
            api_key=api_key or ANTHROPIC_API_KEY
        )
        self._analysis_cache: dict[str, str] = {}

    def analyze_parlay(
        self,
        slip: ParlaySlip,
        context: dict = None,
        use_thinking: bool = True,
    ) -> dict:
        """
        Deep analysis of a parlay slip using Claude Opus with adaptive thinking.
        Returns structured analysis with verdict and confidence.
        """
        prompt = _build_parlay_prompt(slip, context)
        cache_key = f"parlay_{hash(slip.brief())}"

        if cache_key in self._analysis_cache:
            return {"analysis": self._analysis_cache[cache_key], "cached": True}

        try:
            params = {
                "model": CLAUDE_MODEL,
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            }
            if use_thinking:
                params["thinking"] = {"type": "adaptive"}

            with self.client.messages.stream(**params) as stream:
                full_text = ""
                thinking_text = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "thinking_delta":
                            thinking_text += event.delta.thinking
                        elif event.delta.type == "text_delta":
                            full_text += event.delta.text

            # Parse verdict from response
            verdict = self._extract_verdict(full_text)
            confidence = self._extract_confidence(full_text)

            self._analysis_cache[cache_key] = full_text

            return {
                "analysis": full_text,
                "thinking_summary": thinking_text[:500] if thinking_text else "",
                "verdict": verdict,
                "ai_confidence": confidence,
                "cached": False,
            }

        except anthropic.AuthenticationError:
            return {
                "analysis": "⚠️ ANTHROPIC_API_KEY not configured. Set it in your .env file to enable AI analysis.",
                "verdict": "UNAVAILABLE",
                "ai_confidence": "none",
                "cached": False,
            }
        except Exception as e:
            return {
                "analysis": f"AI analysis error: {str(e)}",
                "verdict": "ERROR",
                "ai_confidence": "none",
                "cached": False,
            }

    def analyze_bet(self, bet: BetEvaluation) -> dict:
        """Quick single-bet analysis."""
        prompt = _build_single_bet_prompt(bet)

        try:
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=512,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": prompt}],
            )
            text = next(
                (b.text for b in response.content if b.type == "text"), ""
            )
            return {
                "analysis": text,
                "verdict": self._extract_verdict(text),
                "ai_confidence": self._extract_confidence(text),
            }
        except Exception as e:
            return {"analysis": f"Error: {e}", "verdict": "ERROR", "ai_confidence": "none"}

    def compare_parlays(self, slips: list[ParlaySlip]) -> str:
        """Compare multiple parlay options and recommend the best one."""
        if not slips:
            return "No parlays to compare."

        summaries = []
        for i, slip in enumerate(slips[:5], 1):
            summaries.append(
                f"Option {i} [{slip.grade}]: {slip.brief()}\n"
                f"  Correlation: {slip.correlation_score:.3f} | "
                f"Stake: {slip.recommended_stake_pct:.2%}"
            )

        prompt = f"""You are a professional sports bettor. Compare these {len(summaries)} parlay options
and recommend the single best one for today.

{chr(10).join(summaries)}

In 100 words: Which option and why? Consider EV, win probability, correlation, and risk-reward."""

        try:
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return next((b.text for b in response.content if b.type == "text"), "")
        except Exception as e:
            return f"Comparison error: {e}"

    def get_injury_impact(self, sport: str, team: str, injuries: list[str]) -> str:
        """Ask Claude to assess injury impact on a team."""
        if not injuries:
            return "No significant injuries reported."

        prompt = f"""For a {sport} game featuring the {team}, assess the impact of these injuries:
{', '.join(injuries)}

In 50 words: How much does this hurt {team}'s chances? What position/stat category is most affected?"""

        try:
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            return next((b.text for b in response.content if b.type == "text"), "")
        except Exception:
            return "Injury assessment unavailable."

    def _extract_verdict(self, text: str) -> str:
        """Parse the verdict from Claude's response."""
        text_upper = text.upper()
        for v in ["STRONG PLAY", "MODERATE PLAY", "AVOID", "PASS", "BET", "FADE"]:
            if v in text_upper:
                return v
        return "INCONCLUSIVE"

    def _extract_confidence(self, text: str) -> str:
        """Parse confidence tier from Claude's response."""
        text_lower = text.lower()
        if any(w in text_lower for w in ["strong conviction", "high confidence", "very confident"]):
            return "high"
        if any(w in text_lower for w in ["moderate", "reasonable", "decent"]):
            return "medium"
        if any(w in text_lower for w in ["cautious", "uncertain", "risky", "pass"]):
            return "low"
        return "medium"


# ─── Convenience singleton ────────────────────────────────────────────────────
_advisor: Optional[AIAdvisor] = None


def get_advisor() -> AIAdvisor:
    global _advisor
    if _advisor is None:
        _advisor = AIAdvisor()
    return _advisor
