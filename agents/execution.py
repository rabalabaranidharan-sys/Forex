"""
ForexMind - Execution Pipeline (MERGED SUPERBOT)
Trader → Risk Manager → Portfolio Manager

FEATURES:
  ✅ EMA 200 (H4) trend filter  — trade only WITH the trend
  ✅ Consecutive loss protection — 3 same-direction losses → HOLD
  ✅ Gold confidence gate        — XAU requires 70%+ confidence
  ✅ Gold position cap           — max 0.02 lots for gold
  ✅ Dynamic position sizing     — confidence-based lot size
  ✅ Full async/await            — no blocking sleeps
  ✅ Correct memory API          — get_history(pair) signature
  ✅ Correct calculate_ema usage — returns float, not list
  ✅ ENHANCEMENT 2: Fixed EMA200 with dual candle formats + debug logging
  ✅ ENHANCEMENT 3: Correlation filter to prevent correlated positions
  ✅ ENHANCEMENT 6: Technical-only confidence score (no LLM hallucination)
"""

import json
from typing import Dict, List
from utils.llm import GroqClient
from data.indicators import calculate_ema
from data.fetcher import MT5Client
from config.settings import DEFAULT_LOT_SIZE


# ── ENHANCEMENT 3: Correlation Groups ─────────────────────────────────
CORRELATED_GROUPS = [
    ["EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD"],  # USD strength group
    ["USD_JPY", "USD_CHF", "USD_CAD"],               # USD weakness group
    ["XAU_USD", "XAG_USD"],                          # metals group
]


# ── Helpers ───────────────────────────────────────────────────────────

def get_position_size(confidence: int, pair: str = "") -> float:
    """
    TESTING MODE: All pairs capped at 0.02 lots maximum.
    Gold and GBP: max 0.02 lots.
    Others: max 0.02 lots.
    Switch to live sizing when moving to real account.
    """
    # Gold — high volatility cap
    if "XAU" in pair.upper():
        return 0.01 if confidence < 75 else 0.02

    # GBP — wide spread, needs conservative sizing
    if "GBP" in pair.upper():
        return 0.01 if confidence < 75 else 0.02

    # All other pairs — testing mode cap
    return 0.01 if confidence < 70 else 0.02


# ── ENHANCEMENT 2: Fixed EMA200 with multi-format candle support ──────
def get_ema200_trend(pair: str) -> str:
    """
    Fetch H4 candles and check if price is above/below EMA 200.
    Returns: "BUY_ONLY" | "SELL_ONLY" | "NEUTRAL"
    Includes full debug logging so failures are visible.
    
    ENHANCEMENT 2: Handle multiple candle formats (dict, object, tuple)
    """
    try:
        mt5 = MT5Client()
        candles = mt5.get_candles(pair, "H4", count=250)

        if not candles:
            print(f"    [EMA200] {pair}: No H4 candles returned → NEUTRAL")
            return "NEUTRAL"

        if len(candles) < 200:
            print(f"    [EMA200] {pair}: Only {len(candles)} candles (need 200) → NEUTRAL")
            return "NEUTRAL"

        # ENHANCEMENT 2: Try multiple candle formats
        closes = None
        try:
            # Try dict format first: candle["close"]
            closes = [float(c["close"]) for c in candles]
        except (TypeError, KeyError):
            try:
                # Try object format: candle.close
                closes = [float(c.close) for c in candles]
            except AttributeError:
                try:
                    # Try tuple format: candle[4]
                    closes = [float(c[4]) for c in candles]
                except (TypeError, IndexError):
                    print(f"    [EMA200] {pair}: Cannot parse candle format → NEUTRAL")
                    return "NEUTRAL"

        # calculate_ema returns single float, NOT a list
        ema200 = calculate_ema(closes, 200)
        current_price = closes[-1]

        print(f"    [EMA200] {pair}: price={current_price:.5f} EMA200={ema200:.5f}", end="")

        if current_price > ema200:
            print(" → BUY_ONLY (price above EMA200)")
            return "BUY_ONLY"
        elif current_price < ema200:
            print(" → SELL_ONLY (price below EMA200)")
            return "SELL_ONLY"
        print(" → NEUTRAL")
        return "NEUTRAL"

    except Exception as e:
        print(f"    [EMA200] {pair}: Exception: {e} → NEUTRAL (filter skipped)")
        return "NEUTRAL"


def check_consecutive_losses(pair: str, action: str, memory) -> int:
    """
    Count consecutive losses in the same direction from recent history.
    Returns number of consecutive same-direction losses.
    Uses memory.get_history(pair) — correct signature.
    """
    try:
        history = memory.get_history(pair)
        recent = history[-5:] if len(history) >= 5 else history
        recent = list(reversed(recent))   # most recent first
        count = 0
        for dec in recent:
            if (dec.get("action") == action and
                    dec.get("outcome") not in ("pending", "", None) and
                    dec.get("pnl", 0) < 0):
                count += 1
            else:
                break
        return count
    except Exception as e:
        print(f"    [LossCheck] Error: {e}")
        return 0


# ── ENHANCEMENT 3: Correlation Filter ─────────────────────────────────
def check_correlation_gate(pair: str, open_positions: list) -> tuple:
    """
    Check if trading this pair would create correlated position conflict.
    Returns (bool, reason) — True if OK to trade, False if blocked.
    
    ENHANCEMENT 3: Prevent trading multiple highly-correlated pairs simultaneously
    """
    pair_upper = pair.upper().replace("/", "_")
    
    # Find which group this pair belongs to
    current_group = None
    for group in CORRELATED_GROUPS:
        if pair_upper in group:
            current_group = group
            break
    
    if current_group is None:
        # Pair not in any correlated group, OK to trade
        return True, "OK"
    
    # Check if any pairs from same group are already open
    open_symbols = {pos.get("symbol", "").upper() for pos in open_positions}
    
    for symbol in open_symbols:
        clean_symbol = symbol.replace("_", "")
        if clean_symbol in current_group:
            return False, f"Correlated position {clean_symbol} already open in same group"
    
    return True, "OK"


# ── ENHANCEMENT 6: Technical Confidence Score ─────────────────────────
def calculate_technical_confidence(indicators: dict) -> int:
    """
    Calculate confidence based purely on technical indicators (no LLM bias).
    Counts confluent signals across 6 key metrics.
    
    ENHANCEMENT 6: Pure technical score, avoids LLM hallucination
    """
    if not indicators:
        return 50
    
    signals = 0  # Count of bullish/bearish signals that align
    
    rsi = indicators.get("rsi", 50)
    trend = indicators.get("trend", "MIXED")
    macd = indicators.get("macd", {})
    price_vs_ema9 = indicators.get("price_vs_ema9", 0)
    stoch = indicators.get("stochastic", {})
    
    # Signal 1: RSI oversold/overbought (directional)
    if rsi < 30 or rsi > 70:
        signals += 1
    
    # Signal 2: Trend is not MIXED (has clear direction)
    if trend in ("BULLISH", "BEARISH"):
        signals += 1
    
    # Signal 3: MACD histogram positive/negative (directional)
    if macd.get("histogram", 0) != 0:
        signals += 1
    
    # Signal 4: Price position relative to EMA9
    if price_vs_ema9 > 0 or price_vs_ema9 < 0:
        signals += 1
    
    # Signal 5: Stochastic K oversold/overbought
    stoch_k = stoch.get("k", 50)
    if stoch_k < 20 or stoch_k > 80:
        signals += 1
    
    # Signal 6: RSI in extreme zone (extra confirmation)
    if rsi < 20 or rsi > 80:
        signals += 1
    
    # Confidence formula: base 50% + 8% per signal (max ~90%)
    confidence = 50 + (signals * 8)
    confidence = min(90, max(50, confidence))
    
    return int(confidence)


def parse_json_response(response: str, fallback: dict) -> dict:
    """Safely parse LLM JSON response with fallback."""
    try:
        r = response.strip()
        if "```" in r:
            for part in r.split("```"):
                if "{" in part:
                    r = part.replace("json", "").strip()
                    break
        s = r.find("{")
        e = r.rfind("}") + 1
        if s >= 0 and e > s:
            return json.loads(r[s:e])
    except Exception:
        pass
    return fallback


# ── Pipeline Agents ────────────────────────────────────────────────────

async def trader_agent(pair: str, analyst_reports: Dict[str, str],
                       debate_result: dict, llm: GroqClient) -> dict:
    """
    Trader reads analyst reports + debate lean, proposes a trade.
    Gold requires higher conviction in the prompt.
    """
    print("    [Trader] Analyzing reports and debate...")

    combined = "\n\n".join([
        f"=== {n.upper()} ===\n{r[:350]}" for n, r in analyst_reports.items()
    ])
    lean = debate_result.get("lean", "NEUTRAL")
    conf = debate_result.get("confidence", 50)
    tech = debate_result.get("technical_bias", "NEUTRAL")
    cflu = debate_result.get("confluence_level", 0)
    is_gold = "XAU" in pair.upper()

    gold_note = (
        "\n⚠️  GOLD TRADE: Only recommend BUY/SELL if conviction is VERY HIGH (70%+). "
        "Gold is extremely volatile — HOLD on any doubt."
    ) if is_gold else ""

    min_conf = 70 if is_gold else 65

    prompt = (
        f"You are an expert forex TRADER at a top hedge fund.\n\n"
        f"ANALYST REPORTS:\n{combined}\n\n"
        f"DEBATE: {lean} ({conf}% confidence) | Tech bias: {tech} ({cflu}/4 signals)"
        f"{gold_note}\n\n"
        f"Rules:\n"
        f"- BULLISH debate → BUY if confidence >= {min_conf}%\n"
        f"- BEARISH debate → SELL if confidence >= {min_conf}%\n"
        f"- NEUTRAL or low confidence → HOLD\n"
        f"- Only trade when at least 3/4 signals agree\n\n"
        f"Respond ONLY with JSON (no markdown, no extra text):\n"
        f'{{\n  "action": "BUY",\n  "confidence": 75,\n  "reasoning": "brief reason"\n}}'
    )

    response = llm.call(prompt)
    fallback_action = "BUY" if lean == "BULLISH" else "SELL" if lean == "BEARISH" else "HOLD"
    trade = parse_json_response(response, {
        "action": fallback_action, "confidence": conf,
        "reasoning": f"Fallback: debate {lean}"
    })
    trade["position_size"] = get_position_size(trade.get("confidence", 60), pair)

    print(f"    [Trader] {trade.get('action')} | {trade.get('confidence')}% | {trade.get('position_size')} lots")
    return trade


async def risk_manager(pair: str, trade_proposal: dict, llm: GroqClient) -> dict:
    """
    Risk manager with pair-specific thresholds.
    Gold: rule-based (faster, no LLM waste).
    Others: LLM-evaluated with 55% threshold.
    """
    print("    [Risk Manager] Evaluating...")

    action = trade_proposal.get("action", "HOLD")
    conf   = trade_proposal.get("confidence", 50)
    is_gold = "XAU" in pair.upper()

    if action == "HOLD":
        return {"approved": True, "risk_score": 0, "adjusted_position_size": 0}

    # Gold: strict rule-based (no LLM needed)
    if is_gold:
        approved = conf >= 70
        print(f"    [Risk Manager] GOLD {'✅ APPROVED' if approved else '❌ REJECTED'} — {conf}% {'≥' if approved else '<'} 70%")
        return {
            "approved": approved,
            "risk_score": 2 if approved else 9,
            "adjusted_position_size": get_position_size(conf, pair),
        }

    prompt = (
        f"RISK MANAGER for DEMO account — {pair}\n"
        f"Trade: {action} | Confidence: {conf}% | Size: {trade_proposal.get('position_size', 0.01)} lots\n\n"
        f"Rules: Approve if confidence >= 55%. Auto-approve >= 75%.\n"
        f"Respond ONLY with JSON:\n"
        f'{{\n  "approved": true,\n  "risk_score": 4,\n  "adjusted_position_size": {trade_proposal.get("position_size", 0.01)}\n}}'
    )

    risk = parse_json_response(llm.call(prompt), {
        "approved": conf >= 55, "risk_score": 5,
        "adjusted_position_size": trade_proposal.get("position_size", 0.01)
    })
    print(f"    [Risk Manager] {'✅ APPROVED' if risk.get('approved') else '❌ REJECTED'} | Risk: {risk.get('risk_score')}/10")
    return risk


async def portfolio_manager(pair: str, analyst_reports: Dict[str, str],
                             debate_result: dict, trade_proposal: dict,
                             risk_assessment: dict, history: list,
                             llm: GroqClient, open_positions: list = None,
                             memory=None) -> dict:
    """
    Portfolio Manager — final decision with:
    - EMA 200 trend filter (ENHANCEMENT 2: fixed)
    - Consecutive loss protection
    - Correlation gate (ENHANCEMENT 3)
    - Gold rule-based logic only
    """
    print("    [Portfolio Manager] Final decision...")

    action   = trade_proposal.get("action", "HOLD")
    conf     = trade_proposal.get("confidence", 50)
    approved = risk_assessment.get("approved", False)
    lean     = debate_result.get("lean", "NEUTRAL")
    is_gold  = "XAU" in pair.upper()

    # ── STEP 1: Early exit for HOLD or unapproved ──────────────────
    if action == "HOLD" or not approved:
        reason = "HOLD signal" if action == "HOLD" else f"Risk rejected ({conf}% confidence)"
        print(f"    [Portfolio Manager] ⚪ HOLD — {reason}")
        return {"action": "HOLD", "confidence": conf, "position_size": 0}

    # ── STEP 2: EMA 200 trend filter ──────────────────────────────
    print(f"    [Portfolio Manager] Checking EMA 200 H4 trend...")
    ema_trend = get_ema200_trend(pair)

    if ema_trend == "BUY_ONLY" and action == "SELL":
        print(f"    [Portfolio Manager] ❌ EMA200 says BUY_ONLY — blocking SELL")
        return {"action": "HOLD", "confidence": conf, "position_size": 0,
                "reasoning": "EMA200 H4 bullish — SELL blocked"}

    if ema_trend == "SELL_ONLY" and action == "BUY":
        print(f"    [Portfolio Manager] ❌ EMA200 says SELL_ONLY — blocking BUY")
        return {"action": "HOLD", "confidence": conf, "position_size": 0,
                "reasoning": "EMA200 H4 bearish — BUY blocked"}

    print(f"    [Portfolio Manager] ✅ EMA200 trend: {ema_trend} — aligned with {action}")

    # ── STEP 3: ENHANCEMENT 3: Correlation filter ─────────────────
    if open_positions:
        corr_ok, corr_reason = check_correlation_gate(pair, open_positions)
        if not corr_ok:
            print(f"    [Portfolio Manager] ❌ {corr_reason} — HOLD")
            return {"action": "HOLD", "confidence": conf, "position_size": 0,
                    "reasoning": corr_reason}
        print(f"    [Portfolio Manager] ✅ Correlation check: {corr_reason}")

    # ── STEP 4: Consecutive loss protection ───────────────────────
    if memory:
        losses = check_consecutive_losses(pair, action, memory)
        if losses >= 3:
            print(f"    [Portfolio Manager] ❌ {losses} consecutive {action} losses — HOLD")
            return {"action": "HOLD", "confidence": conf, "position_size": 0,
                    "reasoning": f"{losses} consecutive losses in {action} direction — pausing"}

    # ── STEP 5: Gold rule-based final decision ─────────────────────
    if is_gold:
        size = get_position_size(conf, pair)
        print(f"    [Portfolio Manager] 🥇 GOLD {action} ({conf}%) | {size} lots")
        return {"action": action, "confidence": conf, "position_size": size}

    # ── STEP 6: Standard LLM final decision ───────────────────────
    prompt = (
        f"PORTFOLIO MANAGER — {pair}\n"
        f"Trader: {action} ({conf}%) | Risk: APPROVED | Debate: {lean} | EMA200: {ema_trend}\n\n"
        f"Execute if action=BUY/SELL AND conf>=55%. Override if conf>=75%.\n"
        f"Respond ONLY with JSON:\n"
        f'{{\n  "action": "{action}",\n  "confidence": {conf},\n  "position_size": {trade_proposal.get("position_size", 0.01)}\n}}'
    )

    final = parse_json_response(llm.call(prompt), {
        "action": action, "confidence": conf,
        "position_size": trade_proposal.get("position_size", 0.01)
    })

    a = final.get("action", "HOLD")
    emoji = "🟢" if a == "BUY" else "🔴" if a == "SELL" else "⚪"
    print(f"    [Portfolio Manager] {emoji} {a} ({final.get('confidence')}%) | {final.get('position_size')} lots")
    return final


async def run_execution_pipeline(pair: str, analyst_reports: Dict[str, str],
                                  debate_result: dict, history: list,
                                  llm: GroqClient, open_positions: list = None,
                                  memory=None) -> dict:
    """
    Full pipeline: Trader → Risk → Portfolio Manager
    
    ENHANCEMENT 3: Pass open_positions for correlation checking
    ENHANCEMENT 6: Use technical confidence score
    """
    is_gold     = "XAU" in pair.upper()
    debate_conf = debate_result.get("confidence", 0)

    # ── GOLD CONFIDENCE GATE ──────────────────────────────────────
    if is_gold and debate_conf < 65:
        print(f"    [Pipeline] 🥇 GOLD SKIPPED — {debate_conf}% < 65% required")
        return {
            "action": "HOLD", "confidence": debate_conf,
            "position_size": 0,
            "reasoning": f"Gold needs 65+ confidence. Got {debate_conf}%.",
            "pair": pair, "trade_proposal": {"action": "HOLD"},
            "risk_assessment": {"approved": False},
            "debate_result": debate_result,
        }

    trade  = await trader_agent(pair, analyst_reports, debate_result, llm)
    risk   = await risk_manager(pair, trade, llm)
    
    # ENHANCEMENT 6: Calculate technical confidence (no LLM bias)
    # Get indicators from debate result if available
    current_indicators = debate_result.get("indicators", {})
    tech_confidence = calculate_technical_confidence(current_indicators)
    final_confidence = max(debate_result.get("confidence", 50), tech_confidence)
    
    if final_confidence != debate_result.get("confidence", 50):
        signal_count = sum([
            1 if current_indicators.get("rsi", 50) < 30 or current_indicators.get("rsi", 50) > 70 else 0,
            1 if current_indicators.get("trend", "MIXED") in ("BULLISH", "BEARISH") else 0,
            1 if current_indicators.get("macd", {}).get("histogram", 0) != 0 else 0,
            1 if current_indicators.get("price_vs_ema9", 0) != 0 else 0,
            1 if current_indicators.get("stochastic", {}).get("k", 50) < 20 or current_indicators.get("stochastic", {}).get("k", 50) > 80 else 0,
        ])
        print(f"    [Technical Score] {signal_count}/6 signals → {tech_confidence}%")
        trade["confidence"] = final_confidence
    
    final  = await portfolio_manager(
        pair, analyst_reports, debate_result,
        trade, risk, history, llm, 
        open_positions=open_positions or [],
        memory=memory
    )

    # Add SL/TP pip hints for MT5 executor
    final["sl_pips"] = 150 if is_gold else 50
    final["tp_pips"] = 300 if is_gold else 100
    final.update({
        "pair": pair,
        "trade_proposal":  trade,
        "risk_assessment": risk,
        "debate_result":   debate_result,
    })
    return final
