"""
ForexMind — MT5 Demo Order Executor (ATR-BASED SL/TP)
ENHANCEMENT 1: Dynamic SL/TP based on ATR volatility
"""

import time
from datetime import datetime

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from config.settings import DEFAULT_LOT_SIZE


class MT5Executor:

    def __init__(self, telegram=None):
        self.telegram  = telegram
        self.connected = self._check_connection()

    def _check_connection(self) -> bool:
        if not MT5_AVAILABLE:
            print("  [MT5 Executor] MetaTrader5 not available")
            return False
        if not mt5.initialize():
            print(f"  [MT5 Executor] Not connected: {mt5.last_error()}")
            return False
        print("  [MT5 Executor] Connected to MT5 ✓")
        return True

    def _get_filling_mode(self, symbol):
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5.ORDER_FILLING_IOC
        filling = info.filling_mode
        if filling & 1:
            return mt5.ORDER_FILLING_FOK
        elif filling & 2:
            return mt5.ORDER_FILLING_IOC
        else:
            return mt5.ORDER_FILLING_RETURN

    def place_order(self, pair: str, action: str, size: float,
                    sl: float, tp: float, atr: float = 0.0) -> dict:

        if not self.connected or not MT5_AVAILABLE:
            return {"success": False, "reason": "MT5 not connected"}

        if action == "HOLD":
            return {"success": False, "reason": "HOLD signal"}

        symbol = pair.replace("_", "")
        if not mt5.symbol_select(symbol, True):
            return {"success": False, "reason": f"Cannot select {symbol}"}

        time.sleep(0.5)

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return {"success": False, "reason": f"Symbol {symbol} not found"}

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"success": False, "reason": "Cannot get price"}

        if action == "BUY":
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid

        digits = symbol_info.digits
        point = symbol_info.point

        try:
            sl = float(sl) if sl and sl not in [0, "N/A", "", None] else 0.0
            tp = float(tp) if tp and tp not in [0, "N/A", "", None] else 0.0
        except (ValueError, TypeError):
            sl = 0.0
            tp = 0.0

        # ── ENHANCEMENT 1: ATR-Based SL/TP ────────────────────────────
        is_gold = symbol.upper().startswith('XAU')
        
        if atr > 0:
            # Dynamic ATR-based: SL = 1.5x ATR, TP = 3.0x ATR
            sl_dist = round(atr * 1.5, 5)
            tp_dist = round(atr * 3.0, 5)
            
            if action == "BUY":
                sl = round(price - sl_dist, digits)
                tp = round(price + tp_dist, digits)
            else:  # SELL
                sl = round(price + sl_dist, digits)
                tp = round(price - tp_dist, digits)
            
            print(f"  [MT5 Executor] ATR={atr:.5f} | SL_dist={sl_dist:.5f} | TP_dist={tp_dist:.5f}")
        
        elif is_gold:
            # Gold without ATR: use wider fixed levels
            if action == "BUY":
                sl = round(price - 15.0, digits)
                tp = round(price + 30.0, digits)
            else:  # SELL
                sl = round(price + 15.0, digits)
                tp = round(price - 30.0, digits)
        
        else:
            # Other pairs without ATR: standard formula
            sl_pips = 50
            tp_pips = 100
            
            if action == "BUY":
                if sl == 0.0 or sl >= price:
                    sl = round(price - (sl_pips * point * 10), digits)
                if tp == 0.0 or tp <= price:
                    tp = round(price + (tp_pips * point * 10), digits)
            else:
                if sl == 0.0 or sl <= price:
                    sl = round(price + (sl_pips * point * 10), digits)
                if tp == 0.0 or tp >= price:
                    tp = round(price - (tp_pips * point * 10), digits)

        sl = round(sl, digits)
        tp = round(tp, digits)

        # ── ENHANCEMENT 1: Hard cap lot size at 0.02 (testing mode) ────
        size = min(max(float(size), 0.01), 0.02)
        size = round(size, 2)

        filling = self._get_filling_mode(symbol)

        print(f"  [MT5 Executor] {action} {size} lots {symbol} @ {price}")
        print(f"  [MT5 Executor] SL: {sl} | TP: {tp} | Fill: {filling}")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": size,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 30,
            "magic": 234000,
            "comment": "ForexMind AI",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = mt5.order_send(request)

        if result is None:
            error = mt5.last_error()
            print(f"  [MT5 Executor] Order send failed: {error}")
            return {"success": False, "reason": str(error)}

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = result.order
            print(f"  [MT5 Executor] ✅ Order placed! Ticket #{ticket}")
            if self.telegram:
                self.telegram.send_order(
                    pair=pair, action=action, price=price,
                    sl=sl, tp=tp, size=size, ticket=ticket
                )
            return {
                "success": True,
                "ticket": ticket,
                "price": price,
                "size": size,
                "action": action,
                "sl": sl,
                "tp": tp,
            }
        else:
            reason = f"Retcode: {result.retcode} — {result.comment}"
            print(f"  [MT5 Executor] ❌ Failed: {reason}")
            if result.retcode == 10030:
                print("  [MT5 Executor] Retrying with ORDER_FILLING_RETURN...")
                request["type_filling"] = mt5.ORDER_FILLING_RETURN
                result2 = mt5.order_send(request)
                if result2 and result2.retcode == mt5.TRADE_RETCODE_DONE:
                    ticket = result2.order
                    print(f"  [MT5 Executor] ✅ Order placed with RETURN! Ticket #{ticket}")
                    if self.telegram:
                        self.telegram.send_order(
                            pair=pair, action=action, price=price,
                            sl=sl, tp=tp, size=size, ticket=ticket
                        )
                    return {
                        "success": True,
                        "ticket": ticket,
                        "price": price,
                        "size": size,
                        "action": action,
                        "sl": sl,
                        "tp": tp,
                    }
            return {"success": False, "reason": reason}

    def get_open_positions(self, pair: str = None) -> list:
        if not self.connected or not MT5_AVAILABLE:
            return []
        positions = mt5.positions_get(comment="ForexMind AI")
        if positions is None:
            return []
        result = []
        for p in positions:
            if pair is None or p.symbol == pair.replace("_", ""):
                result.append({
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume,
                    "price": p.price_open,
                    "sl": p.sl,
                    "tp": p.tp,
                    "profit": round(p.profit, 2),
                    "time": datetime.fromtimestamp(p.time).strftime("%Y-%m-%d %H:%M"),
                })
        return result

    def close_position(self, ticket: int) -> dict:
        if not self.connected or not MT5_AVAILABLE:
            return {"success": False}
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return {"success": False, "reason": "Position not found"}
        pos = position[0]
        symbol = pos.symbol
        tick = mt5.symbol_info_tick(symbol)
        filling = self._get_filling_mode(symbol)

        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            close_price = tick.bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            close_price = tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": close_price,
            "deviation": 30,
            "magic": 234000,
            "comment": "ForexMind Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            pnl = round(pos.profit, 2)
            print(f"  [MT5 Executor] ✅ Position #{ticket} closed. P&L: {pnl}")
            if self.telegram:
                self.telegram.send_order_closed(
                    pair=symbol, ticket=ticket,
                    pnl=pnl, reason="ForexMind close"
                )
            return {"success": True, "pnl": pnl}
        return {"success": False, "reason": str(result.retcode if result else "Unknown")}
