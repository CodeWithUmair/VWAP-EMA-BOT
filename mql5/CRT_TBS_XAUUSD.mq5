//+------------------------------------------------------------------+
//|                                             CRT_TBS_XAUUSD.mq5    |
//|   CRT + TBS (Candle Range Theory + Turtle Soup) for XAUUSD        |
//|                                                                   |
//|   A direct port of trading_bot/strategies/crt_tbs.py so the MT5   |
//|   Strategy Tester and the Python backtester describe the same     |
//|   strategy. Same rules, same defaults, same causality:            |
//|                                                                   |
//|     * CRT range  = High / Low / midpoint of the last COMPLETED    |
//|                    reference candle (H1 by default).              |
//|     * TBS entry  = an execution candle (M5 by default) wicks      |
//|                    through the CRT boundary and closes back       |
//|                    inside the range.                              |
//|     * SL         = beyond the sweep wick + buffer.                |
//|     * TP1        = CRT equilibrium (half off, stop to entry).     |
//|     * TP2        = the opposite side of the CRT range.            |
//|                                                                   |
//|   Everything is evaluated on CLOSED candles only, and orders go   |
//|   in at the next candle's open, so a Strategy Tester run on       |
//|   "Open prices only" gives the same trades as a tick run.         |
//+------------------------------------------------------------------+
#property copyright "VWAP-EMA-BOT"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//--- Timeframes -------------------------------------------------------
input group                "Timeframes"
input ENUM_TIMEFRAMES  InpReferenceTF      = PERIOD_H1;   // CRT reference candle
input ENUM_TIMEFRAMES  InpExecutionTF      = PERIOD_M5;   // Turtle Soup execution candle

//--- Sweep / Turtle Soup trigger -------------------------------------
input group                "Sweep trigger"
input int              InpSweepLookback    = 2;      // Execution candles the sweep may span
input double           InpMinSweepPoints   = 0.30;   // Min depth past the level ($)
input double           InpMaxSweepPoints   = 12.0;   // Deeper than this is a real break ($)

//--- Range quality ----------------------------------------------------
input group                "Range quality"
input double           InpMinRangePoints   = 3.0;    // Skip dead reference candles ($)
input double           InpMaxRangePoints   = 60.0;   // Skip news-bar ranges ($)

//--- Risk -------------------------------------------------------------
input group                "Risk"
input double           InpLots             = 0.10;   // Lot size (>= 2x broker min for TP1 partials)
input double           InpSLBufferPoints   = 1.20;   // SL beyond the sweep wick ($)
input double           InpMinSLPoints      = 1.50;   // Min stop distance ($)
input double           InpMaxSLPoints      = 12.0;   // Max stop distance ($)
input double           InpMinRRtoTP2       = 1.20;   // Reject setups below this R:R
input bool             InpUseTP1Partial    = true;   // Half off at equilibrium, stop to entry

//--- Session filters --------------------------------------------------
input group                "Session filters"
input bool             InpUseKillzones     = true;   // London / New York windows only
input int              InpLondonStartMin   = 420;    // 07:00 UTC
input int              InpLondonEndMin     = 600;    // 10:00 UTC
input int              InpNYStartMin       = 750;    // 12:30 UTC
input int              InpNYEndMin         = 960;    // 16:00 UTC

//--- Housekeeping -----------------------------------------------------
input group                "Housekeeping"
input long             InpMagic            = 9212002;
input int              InpSlippage         = 20;
input bool             InpVerbose          = false;  // Print every rejected setup

CTrade   trade;
datetime g_lastExecBarTime = 0;   // last execution candle already judged
ulong    g_tp1DoneTicket    = 0;  // position whose TP1 has been handled
double   g_pendingTP1       = 0;  // TP1 price of the position currently open

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFillingBySymbol(_Symbol);

   if(InpExecutionTF >= InpReferenceTF)
   {
      Print("ERROR: the execution timeframe must be SMALLER than the reference timeframe.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   PrintFormat("CRT+TBS ready on %s — reference %s, execution %s, lots %.2f",
               _Symbol, EnumToString(InpReferenceTF), EnumToString(InpExecutionTF), InpLots);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Minute-of-day in UTC for a server timestamp.                      |
//| NOTE: MT5 gives you SERVER time, and most brokers run GMT+2/+3.   |
//| TimeGMT() is unavailable in the tester, so the offset is measured |
//| once from the live terminal and reused. If your killzones look    |
//| shifted, set InpLondon*/InpNY* to your server's clock instead.    |
//+------------------------------------------------------------------+
int MinuteOfDayUTC(datetime serverTime)
{
   static int offsetSeconds = INT_MIN;
   if(offsetSeconds == INT_MIN)
   {
      datetime gmt = TimeGMT();
      datetime srv = TimeCurrent();
      offsetSeconds = (gmt > 0 && srv > 0) ? (int)(srv - gmt) : 0;
      // Round to the nearest half hour: brokers use whole/half-hour offsets.
      offsetSeconds = (int)(MathRound(offsetSeconds / 1800.0) * 1800);
   }
   MqlDateTime dt;
   TimeToStruct(serverTime - offsetSeconds, dt);
   return dt.hour * 60 + dt.min;
}

bool InKillzone(int minuteOfDay, string &why)
{
   if(minuteOfDay >= InpLondonStartMin && minuteOfDay <= InpLondonEndMin)
   { why = "London killzone"; return true; }
   if(minuteOfDay >= InpNYStartMin && minuteOfDay <= InpNYEndMin)
   { why = "New York killzone"; return true; }
   why = "outside London / NY killzones";
   return false;
}

//+------------------------------------------------------------------+
//| Clamp a wick-derived stop into the configured distance band.      |
//+------------------------------------------------------------------+
double ClampSL(double entry, double rawSL, bool isShort)
{
   double dist = isShort ? (rawSL - entry) : (entry - rawSL);
   dist = MathMax(dist, InpMinSLPoints);
   dist = MathMin(dist, InpMaxSLPoints);
   return isShort ? (entry + dist) : (entry - dist);
}

//+------------------------------------------------------------------+
bool HasOurPosition(ulong &ticket)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
      { ticket = t; return true; }
   }
   ticket = 0;
   return false;
}

//+------------------------------------------------------------------+
//| TP1 handling: bank half at equilibrium and pull the stop to entry.|
//| At the broker minimum lot there is nothing to halve, so the trade |
//| just goes risk-free on full size — same fallback as the Python    |
//| engine.                                                           |
//+------------------------------------------------------------------+
void ManageOpenPosition()
{
   ulong ticket;
   if(!HasOurPosition(ticket)) { g_pendingTP1 = 0; g_tp1DoneTicket = 0; return; }
   if(g_pendingTP1 <= 0 || g_tp1DoneTicket == ticket) return;
   if(!PositionSelectByTicket(ticket)) return;

   long   type   = PositionGetInteger(POSITION_TYPE);
   double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
   double price  = PositionGetDouble(POSITION_PRICE_CURRENT);
   double volume = PositionGetDouble(POSITION_VOLUME);
   double tp     = PositionGetDouble(POSITION_TP);

   bool reached = (type == POSITION_TYPE_BUY) ? (price >= g_pendingTP1)
                                              : (price <= g_pendingTP1);
   if(!reached) return;

   g_tp1DoneTicket = ticket;

   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double stepLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double half    = MathFloor((volume / 2.0) / stepLot) * stepLot;

   if(InpUseTP1Partial && half >= minLot && (volume - half) >= minLot)
   {
      if(trade.PositionClosePartial(ticket, half))
         PrintFormat("TP1 banked: closed %.2f lots at %.2f (CRT equilibrium)", half, g_pendingTP1);
   }
   else if(InpUseTP1Partial)
      PrintFormat("TP1 reached at %.2f but %.2f lots cannot be halved at min lot %.2f — "
                  "going risk-free on full size instead", g_pendingTP1, volume, minLot);

   if(trade.PositionModify(ticket, NormalizeDouble(entry, _Digits), tp))
      PrintFormat("Stop moved to break-even %.2f; runner rides to TP2 %.2f", entry, tp);
}

//+------------------------------------------------------------------+
//| One evaluation per CLOSED execution candle.                       |
//+------------------------------------------------------------------+
void OnTick()
{
   ManageOpenPosition();

   // Only act when a new execution candle has closed.
   datetime execTime = iTime(_Symbol, InpExecutionTF, 1);
   if(execTime == 0 || execTime == g_lastExecBarTime) return;
   g_lastExecBarTime = execTime;

   ulong openTicket;
   if(HasOurPosition(openTicket)) return;          // one position at a time

   // ---- CRT range from the last COMPLETED reference candle ----------
   double crtHigh = iHigh(_Symbol, InpReferenceTF, 1);
   double crtLow  = iLow (_Symbol, InpReferenceTF, 1);
   if(crtHigh <= 0 || crtLow <= 0) return;

   double crtRange = crtHigh - crtLow;
   double crtEq    = (crtHigh + crtLow) / 2.0;
   if(crtRange < InpMinRangePoints || crtRange > InpMaxRangePoints)
   {
      if(InpVerbose) PrintFormat("Skip: CRT range %.2f outside [%.2f, %.2f]",
                                 crtRange, InpMinRangePoints, InpMaxRangePoints);
      return;
   }

   // ---- Session gate -------------------------------------------------
   string why;
   if(InpUseKillzones && !InKillzone(MinuteOfDayUTC(execTime), why))
   {
      if(InpVerbose) PrintFormat("Skip: %s", why);
      return;
   }

   // ---- The just-closed execution candle and its sweep window -------
   double execClose = iClose(_Symbol, InpExecutionTF, 1);
   double sweepHigh = iHigh (_Symbol, InpExecutionTF, 1);
   double sweepLow  = iLow  (_Symbol, InpExecutionTF, 1);

   // Earlier candles in the window may have made the extreme, but none of
   // them may have CLOSED outside — that is consolidation, not a fakeout.
   bool closedOutsideHigh = false, closedOutsideLow = false;
   for(int k = 2; k <= InpSweepLookback; k++)
   {
      sweepHigh = MathMax(sweepHigh, iHigh(_Symbol, InpExecutionTF, k));
      sweepLow  = MathMin(sweepLow,  iLow (_Symbol, InpExecutionTF, k));
      double c  = iClose(_Symbol, InpExecutionTF, k);
      if(c > crtHigh) closedOutsideHigh = true;
      if(c < crtLow)  closedOutsideLow  = true;
   }

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // ================= SHORT: swept buy-side liquidity ================
   double depthUp = sweepHigh - crtHigh;
   if(depthUp >= InpMinSweepPoints && depthUp <= InpMaxSweepPoints &&
      !closedOutsideHigh && execClose < crtHigh)
   {
      double entry = bid;
      double sl    = ClampSL(entry, sweepHigh + InpSLBufferPoints, true);
      double tp2   = crtLow;
      double risk  = sl - entry;
      double rew   = entry - tp2;
      if(risk > 0 && rew > 0 && (rew / risk) >= InpMinRRtoTP2)
      {
         g_pendingTP1 = InpUseTP1Partial ? crtEq : 0;
         if(trade.Sell(InpLots, _Symbol, 0,
                       NormalizeDouble(sl, _Digits), NormalizeDouble(tp2, _Digits),
                       "CRT_TBS_SELL"))
            PrintFormat("SELL — swept %.2f to %.2f, closed back at %.2f | SL %.2f TP1 %.2f TP2 %.2f (%.2fR)",
                        crtHigh, sweepHigh, execClose, sl, crtEq, tp2, rew / risk);
         else
            g_pendingTP1 = 0;
         return;
      }
      if(InpVerbose) PrintFormat("Skip SELL: R:R %.2f below %.2f", (risk > 0 ? rew / risk : 0), InpMinRRtoTP2);
   }

   // ================= LONG: swept sell-side liquidity ================
   double depthDn = crtLow - sweepLow;
   if(depthDn >= InpMinSweepPoints && depthDn <= InpMaxSweepPoints &&
      !closedOutsideLow && execClose > crtLow)
   {
      double entry = ask;
      double sl    = ClampSL(entry, sweepLow - InpSLBufferPoints, false);
      double tp2   = crtHigh;
      double risk  = entry - sl;
      double rew   = tp2 - entry;
      if(risk > 0 && rew > 0 && (rew / risk) >= InpMinRRtoTP2)
      {
         g_pendingTP1 = InpUseTP1Partial ? crtEq : 0;
         if(trade.Buy(InpLots, _Symbol, 0,
                      NormalizeDouble(sl, _Digits), NormalizeDouble(tp2, _Digits),
                      "CRT_TBS_BUY"))
            PrintFormat("BUY — swept %.2f to %.2f, closed back at %.2f | SL %.2f TP1 %.2f TP2 %.2f (%.2fR)",
                        crtLow, sweepLow, execClose, sl, crtEq, tp2, rew / risk);
         else
            g_pendingTP1 = 0;
         return;
      }
      if(InpVerbose) PrintFormat("Skip BUY: R:R %.2f below %.2f", (risk > 0 ? rew / risk : 0), InpMinRRtoTP2);
   }
}
//+------------------------------------------------------------------+
