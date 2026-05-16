"""
ForexMind - Session-Aware Trading Filter
Prevents trading during low-volume sessions.

ENHANCEMENT 7: Pair-specific optimal session routing

Sessions (UTC):
  Asian:   23:00-08:00  (low volume — skip EUR/GBP)
  London:  08:00-16:00  (high volume — trade all)
  Overlap: 13:00-16:00  (BEST — highest volume)
  NY:      13:00-21:00  (high volume — trade all)
  Closed:  21:00-23:00  (very low — skip all)
"""

from datetime import datetime, timezone


def get_current_session() -> str:
    """Return current session name based on UTC hour."""
    h = datetime.now(timezone.utc).hour
    if 13 <= h < 16:
        return "overlap"   # London + NY overlap — best
    elif 8 <= h < 16:
        return "london"
    elif 13 <= h < 21:
        return "newyork"
    elif 21 <= h < 23:
        return "closed"
    else:
        return "asian"     # 23:00-08:00 UTC


def get_session_info() -> dict:
    session = get_current_session()
    info = {
        "overlap":  {"session_name": "London/NY Overlap", "volume": "HIGHEST", "trade": True},
        "london":   {"session_name": "London Session",    "volume": "HIGH",    "trade": True},
        "newyork":  {"session_name": "New York Session",  "volume": "HIGH",    "trade": True},
        "asian":    {"session_name": "Asian Session",     "volume": "LOW",     "trade": False},
        "closed":   {"session_name": "Market Closed",     "volume": "VERY LOW","trade": False},
    }
    result = info.get(session, info["asian"])
    result["session"] = session
    return result


# ── ENHANCEMENT 7: Pair-specific optimal sessions ──────────────────
BEST_SESSIONS = {
    "EUR_USD": ["london", "overlap"],
    "GBP_USD": ["london", "overlap"],
    "USD_JPY": ["asian", "london", "overlap"],
    "AUD_USD": ["asian", "london"],
    "USD_CAD": ["newyork", "overlap"],
    "USD_CHF": ["london", "overlap"],
    "XAU_USD": ["london", "overlap", "newyork"],  # gold trades all active sessions
    "NZD_USD": ["asian"],
}

# Pairs allowed during Asian session (naturally active)
ASIAN_ALLOWED = {"USD_JPY", "AUD_USD", "NZD_USD", "USD_CAD"}


def should_trade(pair: str) -> tuple:
    """
    Returns (bool, reason) — whether to trade this pair now.
    
    ENHANCEMENT 7: Check if current session is optimal for this pair
    """
    session = get_current_session()
    info    = get_session_info()
    pair_upper = pair.upper().replace("/", "_")

    if session == "closed":
        return False, f"[Session] Market closed (21:00-23:00 UTC) — skipping {pair}"

    # ── ENHANCEMENT 7: Check pair-specific optimal sessions ──────────
    if pair_upper in BEST_SESSIONS:
        optimal_sessions = BEST_SESSIONS[pair_upper]
        if session not in optimal_sessions:
            return False, f"[Session] {pair} not optimal in {session} — waiting for {optimal_sessions}"

    # ── Original logic: Asian session filtering ─────────────────────
    if session == "asian":
        if pair_upper in ASIAN_ALLOWED:
            return True, f"[Session] Asian session — {pair} is JPY/AUD active pair ✓"
        else:
            return False, f"[Session] Asian session low volume — skipping {pair}"

    # London, NY, Overlap — trade everything (unless filtered by BEST_SESSIONS above)
    return True, f"[Session] {info['session_name']} ({info['volume']}) — {pair} ✓"
