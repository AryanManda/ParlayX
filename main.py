"""
ParlayX — AI-powered sports betting parlay analyzer
Usage:
  python main.py                        # Full daily scan (all sports)
  python main.py --sport NBA            # Scan specific sport
  python main.py --sport NBA NFL        # Multiple sports
  python main.py --train                # Train/retrain ML models
  python main.py --bankroll 500         # Set bankroll (default $1000)
  python main.py --legs 4               # Target parlay leg count
  python main.py --no-ai                # Skip Claude AI analysis
  python main.py --demo                 # Run with demo data (no API needed)
"""
import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def get_console():
    if RICH_AVAILABLE:
        return Console()
    return None


def print_header(console=None):
    header = """
╔═══════════════════════════════════════════════════════════╗
║           ParlayX — AI-Powered Parlay Analyzer            ║
║      ML + ELO + Monte Carlo + Claude AI Cross-Reference   ║
╚═══════════════════════════════════════════════════════════╝"""
    if console:
        console.print(header, style="bold cyan")
    else:
        print(header)


def print_ev_bets(ev_bets, console=None):
    if not ev_bets:
        msg = "\n[!] No +EV bets found for today."
        if console:
            console.print(msg, style="yellow")
        else:
            print(msg)
        return

    if RICH_AVAILABLE and console:
        table = Table(title=f"Top +EV Bets ({len(ev_bets)} found)", box=box.ROUNDED)
        table.add_column("Sport", style="cyan", width=6)
        table.add_column("Bet", style="white", width=28)
        table.add_column("Odds", justify="right", width=7)
        table.add_column("Model%", justify="right", width=8)
        table.add_column("Implied%", justify="right", width=9)
        table.add_column("EV%", justify="right", width=7)
        table.add_column("Grade", justify="center", width=6)
        table.add_column("Sharp", justify="center", width=6)

        for b in ev_bets[:15]:
            grade_color = {"A+": "bold green", "A": "green", "B+": "yellow",
                           "B": "yellow", "C": "white", "F": "red"}.get(b.grade, "white")
            table.add_row(
                b.sport,
                b.description[:28],
                f"{int(b.best_odds):+d}",
                f"{b.model_prob:.1%}",
                f"{b.implied_prob:.1%}",
                f"[bold green]{b.ev_pct:+.1f}%[/bold green]",
                f"[{grade_color}]{b.grade}[/{grade_color}]",
                "⚡" if b.sharp_money else "—",
            )
        console.print(table)
    else:
        print(f"\n{'─'*80}")
        print(f"  TOP +EV BETS ({len(ev_bets)} found)")
        print(f"{'─'*80}")
        print(f"{'Sport':<6} {'Bet':<28} {'Odds':>7} {'Model%':>8} {'EV%':>7} {'Grade':>6}")
        print(f"{'─'*80}")
        for b in ev_bets[:15]:
            print(f"{b.sport:<6} {b.description:<28} {int(b.best_odds):>+7d} "
                  f"{b.model_prob:>7.1%} {b.ev_pct:>+6.1f}% {b.grade:>6}")


def print_parlay(slip, rank: int = 1, console=None):
    if not slip:
        return

    if RICH_AVAILABLE and console:
        # Grade color
        grade_colors = {"A+": "bold green", "A": "green", "B+": "yellow",
                        "B": "yellow", "C": "white", "D": "red"}
        grade_style = grade_colors.get(slip.grade, "white")

        panel_title = f"Parlay #{rank}  [{grade_style}]{slip.grade}[/{grade_style}]  Score: {slip.composite_score:.0f}/100"

        content_lines = [
            f"  Payout Odds:      [bold]{slip.combined_american:+.0f}[/bold]  ({slip.combined_decimal:.2f}x)",
            f"  Simulated Win%:   [bold]{slip.simulated_win_prob:.1%}[/bold]  (100K Monte Carlo sims)",
            f"  Model Win%:       {slip.model_win_prob:.1%}  (naive, no correlation)",
            f"  Expected Value:   [bold green]{slip.ev_pct:+.1f}%[/bold green]",
            f"  Correlation:      {slip.correlation_score:.3f}  (lower = more independent)",
            f"  Recommended Stake: [bold]{slip.recommended_stake_pct:.2%}[/bold] of bankroll  (${slip.recommended_stake_dollars:.0f})",
            "",
        ]
        # Leg table
        for i, leg in enumerate(slip.legs, 1):
            content_lines.append(
                f"  Leg {i}: [cyan]{leg.description}[/cyan]  "
                f"[bold]{int(leg.best_odds):+d}[/bold]  "
                f"Model: {leg.model_prob:.1%}  "
                f"EV: [green]{leg.ev_pct:+.1f}%[/green]  "
                f"[{grade_colors.get(leg.grade, 'white')}]{leg.grade}[/{grade_colors.get(leg.grade, 'white')}]"
            )

        if slip.ai_analysis:
            content_lines += [
                "",
                f"  [bold magenta]🤖 AI VERDICT:[/bold magenta] {slip.ai_confidence.upper() if slip.ai_confidence else ''}",
                "",
            ]
            # Truncate long analysis for display
            analysis_preview = slip.ai_analysis[:600] + "..." if len(slip.ai_analysis) > 600 else slip.ai_analysis
            for line in analysis_preview.split("\n")[:15]:
                content_lines.append(f"  {line}")

        console.print(Panel(
            "\n".join(content_lines),
            title=panel_title,
            border_style="green" if slip.grade in ("A+", "A") else "yellow",
            padding=(1, 2),
        ))
    else:
        print(f"\n{'═'*70}")
        print(f"  PARLAY #{rank} | Grade: {slip.grade} | Score: {slip.composite_score:.0f}/100")
        print(f"{'═'*70}")
        print(f"  Payout:         {slip.combined_american:+.0f} ({slip.combined_decimal:.2f}x)")
        print(f"  Win Prob:       {slip.simulated_win_prob:.1%} (Monte Carlo)")
        print(f"  Expected Value: {slip.ev_pct:+.1f}%")
        print(f"  Correlation:    {slip.correlation_score:.3f}")
        print(f"  Stake:          {slip.recommended_stake_pct:.2%} (${slip.recommended_stake_dollars:.0f})")
        print(f"\n  LEGS:")
        for i, leg in enumerate(slip.legs, 1):
            print(f"    {i}. {leg.description:30s} {int(leg.best_odds):+6d}  "
                  f"Model: {leg.model_prob:.1%}  EV: {leg.ev_pct:+.1f}%  [{leg.grade}]")
        if slip.ai_analysis:
            print(f"\n  AI ANALYSIS ({slip.ai_confidence.upper()}):")
            print(f"  {slip.ai_analysis[:500]}...")


def run_demo():
    """Run a demonstration with synthetic data (no API keys needed)."""
    from src.analysis.ev_calculator import evaluate_bet
    from src.parlay.builder import build_parlay
    from src.ai.advisor import get_advisor

    console = get_console()
    print_header(console)

    if console:
        console.print("\n[bold yellow]DEMO MODE[/bold yellow] — Running with synthetic data\n")
    else:
        print("\n[DEMO MODE] Running with synthetic data\n")

    # Create realistic demo bets
    demo_bets = [
        evaluate_bet("NBA", "g1", "moneyline", "home_win", "Lakers ML",
                     0.62, "high", -135, teams="Lakers vs Celtics"),
        evaluate_bet("NFL", "g2", "moneyline", "home_win", "Chiefs ML",
                     0.65, "high", -155, teams="Chiefs vs Bills"),
        evaluate_bet("NBA", "g3", "total", "over", "Over 227.5",
                     0.58, "medium", -108, teams="Nuggets vs Warriors"),
        evaluate_bet("MLB", "g4", "moneyline", "away_win", "Dodgers ML",
                     0.61, "medium", +105, teams="Braves vs Dodgers"),
        evaluate_bet("NHL", "g5", "moneyline", "home_win", "Avalanche ML",
                     0.59, "medium", -120, teams="Avalanche vs Stars"),
        evaluate_bet("NBA", "g6", "spread", "home_cover", "Celtics -4.5",
                     0.56, "medium", -110, teams="Celtics vs Heat"),
    ]

    # Filter +EV
    from src.analysis.ev_calculator import screen_bets
    ev_bets = screen_bets(demo_bets)

    print_ev_bets(ev_bets, console)

    # Build parlay
    if console:
        console.print("\n[bold]Building optimal parlay...[/bold]")
    else:
        print("\nBuilding optimal parlay...")

    from src.analysis.correlation import suggest_uncorrelated_combo
    best_legs = suggest_uncorrelated_combo(ev_bets, n_legs=3)
    slip = build_parlay(best_legs, bankroll=1000.0)

    # Quick AI analysis
    if console:
        console.print("\n[bold magenta]Running Claude AI analysis...[/bold magenta]")
    else:
        print("\nRunning Claude AI analysis...")

    advisor = get_advisor()
    ai_result = advisor.analyze_parlay(slip)
    slip.ai_analysis = ai_result.get("analysis", "")
    slip.ai_confidence = ai_result.get("ai_confidence", "")

    print_parlay(slip, rank=1, console=console)

    if console:
        console.print(
            "\n[dim]To use real data: set ANTHROPIC_API_KEY and ODDS_API_KEY in .env[/dim]"
        )


def main():
    parser = argparse.ArgumentParser(
        description="ParlayX — AI-Powered Sports Parlay Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--sport", nargs="+", default=["NBA", "NFL", "MLB", "NHL"],
                        help="Sports to scan (NBA NFL MLB NHL NCAAB NCAAF)")
    parser.add_argument("--train", action="store_true",
                        help="Train/retrain all ML models")
    parser.add_argument("--bankroll", type=float, default=1000.0,
                        help="Your bankroll in dollars (default: 1000)")
    parser.add_argument("--legs", type=int, default=3,
                        help="Target parlay leg count (2-5, default: 3)")
    parser.add_argument("--no-ai", action="store_true",
                        help="Skip Claude AI analysis (faster)")
    parser.add_argument("--demo", action="store_true",
                        help="Run demo with synthetic data (no API keys needed)")
    parser.add_argument("--top", type=int, default=5,
                        help="Show top N +EV bets (default: 5)")

    args = parser.parse_args()

    console = get_console()
    print_header(console)

    if args.demo:
        run_demo()
        return

    if args.train:
        if console:
            console.print("\n[bold]Training ML models...[/bold]")
        else:
            print("\nTraining ML models...")
        from src.models.probability import train_all_models
        train_all_models(use_synthetic=True)
        if console:
            console.print("[green]✓ All models trained and saved.[/green]")
        else:
            print("All models trained and saved.")
        return

    # Initialize DB
    from src.data.storage import init_db
    init_db()

    # Run full scan
    from src.engine import scan_today
    results = scan_today(
        sports=args.sport,
        ai_analysis=not args.no_ai,
        bankroll=args.bankroll,
        n_parlay_legs=args.legs,
    )

    # Print results
    if console:
        console.print(f"\n[dim]Scanned {results['games_scanned']} games | "
                      f"Evaluated {results['total_bets_evaluated']} bets | "
                      f"Found {results['ev_bets_found']} +EV opportunities[/dim]")
    else:
        print(f"\nScanned {results['games_scanned']} games | "
              f"Evaluated {results['total_bets_evaluated']} bets | "
              f"Found {results['ev_bets_found']} +EV opportunities")

    print_ev_bets(results["top_ev_bets"][:args.top], console)

    if results["best_parlays"]:
        if console:
            console.print(f"\n[bold]Top {len(results['best_parlays'])} Parlay Recommendation(s):[/bold]")
        else:
            print(f"\nTop {len(results['best_parlays'])} Parlay Recommendation(s):")

        for i, slip in enumerate(results["best_parlays"], 1):
            print_parlay(slip, rank=i, console=console)
    else:
        msg = "\n[!] Not enough +EV bets to build a quality parlay today."
        if console:
            console.print(msg, style="yellow")
        else:
            print(msg)

    if console:
        console.print(
            "\n[dim]Tip: Use --train to retrain models | "
            "--bankroll to set stake sizing | --demo for offline testing[/dim]\n"
        )


if __name__ == "__main__":
    main()
