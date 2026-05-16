"""
ForexMind Auto Runner (UPGRADED)
ENHANCEMENT 8: Cycle summary with position status

Changes vs previous:
  + Day-end close at 20:00 UTC (avoids swap charges)
  + 15-min cooldown per pair after trade placed
  + 6 pairs: EUR, GBP, JPY, XAU, AUD, CAD
  + Cycle summary: open positions, P&L, pips performance
"""

import subprocess
import sys
import time
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

PAIRS          = ["EUR_USD", "GBP_USD", "USD_JPY", "XAU_USD", "AUD_USD", "USD_CAD"]
INTERVAL       = 15 * 60   # 15 min between full cycles
TRADE_COOLDOWN = 15 * 60   # 15 min cooldown per pair after trade
ROUNDS         = 2
PAIR_TIMEOUT   = 300        # 5 min max per pair
MAGIC_NUMBER   = 234000

last_trade_time = {}        # pair → timestamp of last trade
last_close_hour = -1        # prevent double-close


def banner():
    print("""
╔═══��══════════════════════════════════════════════════════╗
║        ForexMind SUPERBOT Auto Runner                    ║
║   Cycle: 15 min  |  Trade cooldown: 15 min per pair     ║
║   Day-end close: 20:00 UTC (avoids swap)                ║
║   Pairs: EUR, GBP, JPY, XAU, AUD, CAD                   ║
║   Press Ctrl+C to stop                                   ║
╚══════════════════════════════════════════════════════════╝
""")


# ── ENHANCEMENT 8: Cycle summary with position details ─────────────
def get_pip_size(symbol: str) -> float:
    """Get pip size for a given symbol."""
    symbol_upper = symbol.upper().replace("_", "")
    if "JPY" in symbol_upper:
        return 0.01
    if "XAU" in symbol_upper or "GOLD" in symbol_upper:
        return 0.1
    return 0.0001


def print_cycle_summary(executor) -> dict:
    """
    ENHANCEMENT 8: Print performance summary for current cycle.
    Returns summary dict with counts and totals.
    """
    if not MT5_AVAILABLE or not executor.connected:
        return {"open": 0, "total_pnl": 0}
    
    try:
        positions = mt5.positions_get()
        if not positions:
            print(f"  ══ Open Positions: 0 | Total P&L: $0.00 ══")
            return {"open": 0, "total_pnl": 0}
        
        fm_positions = [p for p in positions if p.magic == MAGIC_NUMBER]
        if not fm_positions:
            print(f"  ══ Open Positions: 0 | Total P&L: $0.00 ══")
            return {"open": 0, "total_pnl": 0}
        
        total_pnl = sum(p.profit for p in fm_positions)
        
        print(f"\n  ══ Cycle Summary ══")
        print(f"  Open positions: {len(fm_positions)} | Total P&L: ${total_pnl:+.2f}")
        
        for pos in fm_positions:
            symbol = pos.symbol
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                continue
            
            # Calculate pips
            pip_size = get_pip_size(symbol)
            if pos.type == 0:  # BUY
                pips = (tick.bid - pos.price_open) / pip_size
                direction = "BUY"
            else:  # SELL
                pips = (pos.price_open - tick.ask) / pip_size
                direction = "SELL"
            
            # Format symbol for display
            display_symbol = f"{symbol[:3]}/{symbol[3:]}" if len(symbol) == 6 else symbol
            
            # P&L sign
            sign = "+" if pos.profit >= 0 else ""
            
            print(f"  {display_symbol}: {direction} #{pos.ticket} | {sign}${pos.profit:.2f} | {pips:+.1f} pips")
        
        print(f"  ═════════════════════════════════════")
        
        return {
            "open": len(fm_positions),
            "total_pnl": round(total_pnl, 2),
        }
    
    except Exception as e:
        print(f"  [AutoRunner] Summary error: {e}")
        return {"open": 0, "total_pnl": 0}


def close_all_positions_eod():
    """Close all open positions at 20:00 UTC for day-end."""
    if not MT5_AVAILABLE:
        print("  [AutoRunner] MT5 not available for EOD close")
        return 0

    if not mt5.initialize():
        return 0

    positions = mt5.positions_get()
    if not positions:
        print("  [AutoRunner] No positions to close at EOD")
        return 0

    fm_positions = [p for p in positions if p.magic == MAGIC_NUMBER]
    if not fm_positions:
        print("  [AutoRunner] No ForexMind positions to close")
        return 0

    closed = 0
    print(f"  [AutoRunner] ⏰ EOD closing {len(fm_positions)} position(s)...")
    for pos in fm_positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            continue
        price      = tick.bid if pos.type == 0 else tick.ask
        order_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "position":  pos.ticket,
            "symbol":    pos.symbol,
            "volume":    pos.volume,
            "type":      order_type,
            "price":     price,
            "deviation": 20,
            "magic":     MAGIC_NUMBER,
            "comment":   "EOD auto-close 20:00 UTC",
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            sign = "+" if pos.profit >= 0 else ""
            print(f"  [AutoRunner] ✅ Closed {pos.symbol} #{pos.ticket} | P&L: {sign}${pos.profit:.2f}")
            closed += 1
        else:
            retcode = result.retcode if result else "None"
            print(f"  [AutoRunner] ❌ Failed {pos.symbol}: {retcode}")

    return closed


def run_pair(pair: str) -> bool:
    """Run main.py for one pair. Returns True if completed successfully."""
    try:
        result = subprocess.run(
            [sys.executable, "main.py", "--pair", pair, "--rounds", str(ROUNDS)],
            capture_output=False,
            timeout=PAIR_TIMEOUT,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ⚠️  {pair} timed out after {PAIR_TIMEOUT//60} min")
        return False
    except Exception as e:
        print(f"  ❌  {pair} error: {e}")
        return False


def main():
    banner()
    cycle = 1
    global last_close_hour

    # Initialize executor for summary function
    from utils.mt5_executor import MT5Executor
    executor = MT5Executor()

    while True:
        now_utc = datetime.now(timezone.utc)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ── Day-end close at 20:00 UTC ──────────────────────────
        if now_utc.hour == 20 and last_close_hour != 20:
            print(f"\n{'='*58}")
            print(f"  ⏰ 20:00 UTC — Day-End Auto Close")
            print(f"{'='*58}")
            closed = close_all_positions_eod()
            if closed > 0:
                print(f"  ✅ Closed {closed} position(s). Resuming at next cycle.")
            last_close_hour = 20
        elif now_utc.hour != 20:
            last_close_hour = -1   # reset for tomorrow

        print(f"\n{'='*58}")
        print(f"  CYCLE #{cycle}  —  {now_str}")
        print(f"{'='*58}")

        for pair in PAIRS:
            now_ts = time.time()

            # Check trade cooldown
            if pair in last_trade_time:
                elapsed = now_ts - last_trade_time[pair]
                if elapsed < TRADE_COOLDOWN:
                    remaining = int((TRADE_COOLDOWN - elapsed) / 60)
                    print(f"  ⏳  {pair} — cooldown {remaining} min remaining")
                    continue

            print(f"\n  ▶ Analyzing {pair}...")
            success = run_pair(pair)

            if success:
                last_trade_time[pair] = time.time()
                print(f"  ✅  {pair} — run complete. 15-min cooldown starts.")
            else:
                print(f"  ➖  {pair} — skipped or error.")

            time.sleep(5)

        # ── ENHANCEMENT 8: Print cycle summary ─────────────────────
        print(f"\n{'='*58}")
        summary = print_cycle_summary(executor)
        next_cycle = cycle + 1
        next_run = datetime.fromtimestamp(time.time() + INTERVAL)
        print(f"  Next cycle: {next_run.strftime('%H:%M:%S')}")
        print(f"{'='*58}\n")

        cycle += 1

        try:
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            print("\n\n  ForexMind stopped. Goodbye!\n")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  ForexMind stopped. Goodbye!\n")
