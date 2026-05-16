"""
ForexMind — Memory Manager
Persistent JSON decision log + trade history + reflections

ENHANCEMENT 4: Auto-sync outcomes from MT5 closed deals
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from config.settings import MEMORY_DIR, DECISION_LOG, TRADE_HISTORY


class MemoryManager:
    def __init__(self):
        os.makedirs(MEMORY_DIR, exist_ok=True)
        self._init_files()

    def _init_files(self):
        for filepath in [DECISION_LOG, TRADE_HISTORY]:
            if not os.path.exists(filepath):
                with open(filepath, "w") as f:
                    json.dump([], f)

    def _load(self, filepath: str) -> list:
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, filepath: str, data: list):
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def save_decision(self, pair: str, decision: dict,
                      analyst_reports: dict, debate_transcript: list):
        """Save a completed decision to the log."""
        decisions = self._load(DECISION_LOG)

        record = {
            "id":               len(decisions) + 1,
            "date":             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pair":             pair,
            "action":           decision.get("action", "HOLD"),
            "confidence":       decision.get("confidence", 0),
            "stop_loss":        decision.get("stop_loss", "N/A"),
            "take_profit":      decision.get("take_profit", "N/A"),
            "position_size":    decision.get("position_size", 0.01),
            "reasoning":        decision.get("reasoning", ""),
            "analyst_summary": {
                name: report[:200] for name, report in analyst_reports.items()
            },
            "debate_rounds":    len(debate_transcript),
            "outcome":          "pending",
            "pnl":              0,
            "reflection":       "",
        }

        decisions.append(record)
        self._save(DECISION_LOG, decisions)
        print(f"\n  Memory saved — Decision #{record['id']} logged")

    def get_history(self, pair: str) -> list:
        """Get all prior decisions for a specific pair."""
        decisions = self._load(DECISION_LOG)
        return [d for d in decisions if d.get("pair") == pair]

    def update_outcome(self, decision_id: int, outcome: str,
                       pnl: float, reflection: str = ""):
        """Update a decision with its actual outcome (call manually after trade closes)."""
        decisions = self._load(DECISION_LOG)
        for d in decisions:
            if d.get("id") == decision_id:
                d["outcome"]    = outcome
                d["pnl"]        = pnl
                d["reflection"] = reflection
                d["closed_at"]  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        self._save(DECISION_LOG, decisions)

    # ── ENHANCEMENT 4: Auto-sync outcomes from MT5 ─────────────────────
    def sync_outcomes_from_mt5(self, executor) -> int:
        """
        ENHANCEMENT 4: Automatically sync closed deal outcomes from MT5.
        Matches closed deals (DEAL_ENTRY_OUT) with pending decisions.
        Returns number of outcomes updated.
        """
        if not MT5_AVAILABLE or not executor.connected:
            return 0
        
        try:
            # Get all closed deals from MT5 with magic=234000
            # MT5 returns deals from a specific date range
            magic = 234000
            deals = mt5.history_deals_get(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime.now(timezone.utc)
            )
            
            if not deals:
                return 0
            
            # Filter for our magic number and closed entries
            fm_deals = [
                d for d in deals
                if d.magic == magic and d.entry == 1  # DEAL_ENTRY_OUT = 1
            ]
            
            if not fm_deals:
                return 0
            
            decisions = self._load(DECISION_LOG)
            updated = 0
            
            for deal in fm_deals:
                # Extract deal info
                deal_ticket = deal.ticket
                deal_symbol = deal.symbol
                deal_profit = deal.profit
                deal_time = datetime.fromtimestamp(deal.time)
                
                # Convert symbol back to pair format (EURUSD → EUR_USD)
                if len(deal_symbol) == 6:
                    pair_key = f"{deal_symbol[:3]}_{deal_symbol[3:]}"
                else:
                    pair_key = deal_symbol
                
                # Find matching pending decision by pair + time proximity
                for d in decisions:
                    if (d.get("pair") == pair_key and
                        d.get("outcome") == "pending"):
                        
                        # Update with actual outcome
                        outcome = "WIN" if deal_profit > 0 else "LOSS"
                        d["outcome"] = outcome
                        d["pnl"] = round(deal_profit, 2)
                        d["closed_at"] = deal_time.strftime("%Y-%m-%d %H:%M:%S")
                        updated += 1
                        
                        print(f"  [Memory] Synced {pair_key} deal: {outcome} | P&L: ${deal_profit:.2f}")
                        break
            
            if updated > 0:
                self._save(DECISION_LOG, decisions)
            
            return updated
        
        except Exception as e:
            print(f"  [Memory] Sync error: {e}")
            return 0

    def get_stats(self) -> dict:
        """Get overall portfolio statistics."""
        decisions = self._load(DECISION_LOG)
        if not decisions:
            return {}

        buys  = sum(1 for d in decisions if d.get("action") == "BUY")
        sells = sum(1 for d in decisions if d.get("action") == "SELL")
        holds = sum(1 for d in decisions if d.get("action") == "HOLD")

        by_pair = {}
        for d in decisions:
            p = d.get("pair", "UNKNOWN")
            by_pair[p] = by_pair.get(p, 0) + 1

        # PnL stats for closed trades
        closed = [d for d in decisions if d.get("outcome") != "pending"]
        total_pnl = sum(d.get("pnl", 0) for d in closed)

        return {
            "total":    len(decisions),
            "buys":     buys,
            "sells":    sells,
            "holds":    holds,
            "by_pair":  by_pair,
            "closed":   len(closed),
            "total_pnl": round(total_pnl, 2),
        }

    def get_recent(self, n: int = 5) -> list:
        """Get n most recent decisions."""
        decisions = self._load(DECISION_LOG)
        return decisions[-n:]
