"""Run this to diagnose ParlayX issues: python diagnose.py"""
import os, sys
from pathlib import Path

ROOT = Path(__file__).parent
print("=" * 55)
print("  ParlayX Diagnostics")
print("=" * 55)

# 1. .env
env_file = ROOT / ".env"
print(f"\n[1] .env file: {'FOUND' if env_file.exists() else 'MISSING'}")
if env_file.exists():
    lines = env_file.read_text().splitlines()
    for line in lines:
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            status = "SET" if v.strip() else "EMPTY"
            print(f"    {k.strip()} = {status}")
else:
    print("    -> Creating .env now...")
    key = input("    Paste your ANTHROPIC_API_KEY: ").strip()
    odds = input("    Paste your ODDS_API_KEY (or press Enter to skip): ").strip()
    env_file.write_text(
        f"ANTHROPIC_API_KEY={key}\nODDS_API_KEY={odds}\nOPENAI_API_KEY=\nDEEPSEEK_API_KEY=\nGEMINI_API_KEY=\n"
    )
    print("    .env created!")

# 2. Load config
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(dotenv_path=env_file)
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
print(f"\n[2] ANTHROPIC_API_KEY loaded: {'YES (' + ANTHROPIC_KEY[:12] + '...)' if ANTHROPIC_KEY else 'NO'}")

# 3. Test Claude API directly
print("\n[3] Testing Claude API...")
if not ANTHROPIC_KEY:
    print("    SKIP — no key")
else:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY, timeout=20.0)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        print(f"    SUCCESS: {msg.content[0].text}")
    except Exception as e:
        print(f"    FAILED: {e}")

# 4. Check server file has per-bot endpoint
print("\n[4] Checking api/server.py...")
server_file = ROOT / "api" / "server.py"
content = server_file.read_text()
if "/ai-picks/{bot}" in content:
    print("    Per-bot endpoint: PRESENT")
else:
    print("    Per-bot endpoint: MISSING — writing fix...")
    # Patch the file
    old = '@app.get("/api/ai-picks")\nasync def get_ai_picks():'
    if old in content:
        content = content.replace(old,
            '@app.get("/api/ai-picks/{bot}")\nasync def get_ai_pick_for_bot(bot: str):')
        server_file.write_text(content)
        print("    Patched!")

# 5. Check multi_advisor uses haiku
print("\n[5] Checking src/ai/multi_advisor.py...")
ma_file = ROOT / "src" / "ai" / "multi_advisor.py"
if ma_file.exists():
    mc = ma_file.read_text()
    if "haiku" in mc:
        print("    Uses fast haiku model: YES")
    else:
        print("    Uses fast haiku model: NO — still on opus (slow!)")
        mc = mc.replace('"claude-opus-4-6"', '"claude-haiku-4-5-20251001"')
        mc = mc.replace("anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)",
                        "anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=25.0)")
        ma_file.write_text(mc)
        print("    Patched to haiku!")
else:
    print("    MISSING — multi_advisor.py not found")

# 6. Check JS has per-bot fetch
print("\n[6] Checking static/js/app.js...")
js_file = ROOT / "static" / "js" / "app.js"
if js_file.exists():
    jc = js_file.read_text()
    if "ai-picks/${bot}" in jc:
        print("    Per-bot fetch: PRESENT")
    else:
        print("    Per-bot fetch: MISSING — old single-call version")
        print("    -> You need the latest app.js from git")
else:
    print("    MISSING")

print("\n" + "=" * 55)
print("  Done. Fix any FAILED/MISSING items above,")
print("  then restart: python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload")
print("=" * 55)
