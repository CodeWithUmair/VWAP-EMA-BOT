import React from "react";
import { CheckCircle2, XCircle, AlertCircle, ArrowUpRight, ArrowDownRight, Zap, ShieldAlert, Sparkles, TrendingUp, TrendingDown } from "lucide-react";
import { ChecklistCondition, AccountData } from "../types";

interface SetupChecklistProps {
  longStatus: ChecklistCondition | null;
  shortStatus: ChecklistCondition | null;
  account: AccountData | null;
  noiseGatePassed: boolean;
  onDispatchOrder: (direction: "BUY" | "SELL", sl: number, tp: number) => void;
}

export const SetupChecklist: React.FC<SetupChecklistProps> = ({
  longStatus,
  shortStatus,
  account,
  noiseGatePassed,
  onDispatchOrder
}) => {
  if (!longStatus || !shortStatus) {
    return (
      <div className="p-8 text-center text-slate-400">
        <div className="animate-spin w-8 h-8 border-2 border-amber-500 border-t-transparent rounded-full mx-auto mb-3" />
        Loading live strategy conditions...
      </div>
    );
  }

  const renderConditionRow = (num: number, title: string, passed: boolean, detail: string, valueStr: string) => (
    <div className={`p-3.5 rounded-lg border transition-all ${
      passed ? "bg-emerald-950/30 border-emerald-800/60" : "bg-slate-800/40 border-slate-700/50"
    }`}>
      <div className="flex items-start justify-between">
        <div className="flex items-start space-x-2.5">
          <div className="mt-0.5">
            {passed ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
            ) : (
              <XCircle className="w-5 h-5 text-slate-500 shrink-0" />
            )}
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="text-xs font-bold text-slate-400">STEP {num}</span>
              <h4 className="text-sm font-semibold text-slate-200">{title}</h4>
            </div>
            <p className="text-xs text-slate-300 mt-0.5">{detail}</p>
          </div>
        </div>
        <div className={`text-xs font-mono px-2 py-0.5 rounded ${
          passed ? "bg-emerald-900/60 text-emerald-300 font-semibold" : "bg-slate-800 text-slate-400"
        }`}>
          {valueStr}
        </div>
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      {/* Noise Gate Banner */}
      {!noiseGatePassed && (
        <div className="p-4 rounded-xl bg-amber-950/40 border border-amber-800/60 text-amber-200 flex items-start space-x-3">
          <ShieldAlert className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
          <div className="text-xs leading-relaxed">
            <strong className="text-amber-300 font-bold block text-sm mb-0.5">
              ⚠️ Noise-Control Gate Status: NOT CLEARED (p &gt; 0.05)
            </strong>
            This strategy currently exhibits high p-value against the shuffled Monte Carlo null distribution. Per the safety mandate, automated trading execution is blocked until backtest optimization passes the noise test. Manual demo testing is available below for causal inspection.
          </div>
        </div>
      )}

      {/* Checklist Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* ================= LONG (BUY) COLUMN ================= */}
        <div className={`rounded-xl border p-5 transition-all ${
          longStatus.all_passed
            ? "bg-emerald-950/20 border-emerald-600/80 shadow-lg shadow-emerald-950/50"
            : "bg-slate-900/70 border-slate-800"
        }`}>
          <div className="flex items-center justify-between pb-4 border-b border-slate-800">
            <div className="flex items-center space-x-2.5">
              <div className="w-8 h-8 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold">
                <TrendingUp className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  BUY Setup Checklist
                  {longStatus.all_passed && (
                    <span className="px-2 py-0.5 text-xs bg-emerald-500 text-slate-950 font-extrabold rounded animate-pulse">
                      SIGNAL ACTIVE
                    </span>
                  )}
                </h3>
                <p className="text-xs text-slate-400">Current Gold Price: <span className="font-mono text-emerald-400">${longStatus.close_price.toFixed(2)}</span></p>
              </div>
            </div>
            <div className="text-right">
              <span className="text-xs text-slate-400">5-Step Status</span>
              <div className="text-sm font-bold text-slate-200">
                {[longStatus.vwap_pass, longStatus.crossover_pass, longStatus.ob_pass, longStatus.pullback_pass, longStatus.confirmation_pass].filter(Boolean).length} / 5 Passed
              </div>
            </div>
          </div>

          <div className="space-y-2.5 mt-4">
            {renderConditionRow(
              1,
              "Trend Filter: Close > VWAP",
              longStatus.vwap_pass,
              longStatus.vwap_detail,
              `VWAP: $${longStatus.vwap_value.toFixed(2)}`
            )}

            {renderConditionRow(
              2,
              "Crossover: EMA 9 crosses above EMA 21",
              longStatus.crossover_pass,
              longStatus.crossover_detail,
              `EMA9: $${longStatus.ema_fast.toFixed(2)} | EMA21: $${longStatus.ema_slow.toFixed(2)}`
            )}

            {renderConditionRow(
              3,
              "Structural: Bullish Order Block Reaction",
              longStatus.ob_pass,
              longStatus.ob_detail,
              longStatus.ob_pass ? "OB Active" : "No OB in Zone"
            )}

            {renderConditionRow(
              4,
              "Pullback: Price retraced near EMAs",
              longStatus.pullback_pass,
              longStatus.pullback_detail,
              longStatus.pullback_pass ? "In Pullback Zone" : "No Retrace"
            )}

            {renderConditionRow(
              5,
              "Confirmation Candle Trigger",
              longStatus.confirmation_pass,
              longStatus.confirmation_detail,
              longStatus.pattern_name || "Waiting Trigger"
            )}
          </div>

          {/* Action Box */}
          <div className="mt-5 pt-4 border-t border-slate-800">
            <div className="grid grid-cols-3 gap-2 text-center text-xs mb-3">
              <div className="bg-slate-800/60 p-2 rounded">
                <span className="text-slate-400 block">Entry Price</span>
                <span className="font-mono font-bold text-slate-200">${longStatus.suggested_entry.toFixed(2)}</span>
              </div>
              <div className="bg-slate-800/60 p-2 rounded">
                <span className="text-rose-400 block">Stop Loss (-1R)</span>
                <span className="font-mono font-bold text-rose-300">${longStatus.suggested_sl.toFixed(2)}</span>
                <span className="text-[10px] text-slate-400 block">(${longStatus.risk_points.toFixed(2)} pts)</span>
              </div>
              <div className="bg-slate-800/60 p-2 rounded">
                <span className="text-emerald-400 block">Take Profit (+2R)</span>
                <span className="font-mono font-bold text-emerald-300">${longStatus.suggested_tp.toFixed(2)}</span>
                <span className="text-[10px] text-slate-400 block">(${longStatus.reward_points.toFixed(2)} pts)</span>
              </div>
            </div>

            <button
              onClick={() => onDispatchOrder("BUY", longStatus.suggested_sl, longStatus.suggested_tp)}
              className={`w-full py-2.5 px-4 rounded-lg font-bold text-xs flex items-center justify-center space-x-2 transition-all ${
                longStatus.all_passed
                  ? "bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-md shadow-emerald-900/40 cursor-pointer"
                  : "bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700"
              }`}
            >
              <Zap className="w-4 h-4" />
              <span>{longStatus.all_passed ? "EXECUTE 0.1 LOT DEMO BUY" : "TEST DEMO BUY ORDER"}</span>
            </button>
          </div>
        </div>

        {/* ================= SHORT (SELL) COLUMN ================= */}
        <div className={`rounded-xl border p-5 transition-all ${
          shortStatus.all_passed
            ? "bg-rose-950/20 border-rose-600/80 shadow-lg shadow-rose-950/50"
            : "bg-slate-900/70 border-slate-800"
        }`}>
          <div className="flex items-center justify-between pb-4 border-b border-slate-800">
            <div className="flex items-center space-x-2.5">
              <div className="w-8 h-8 rounded-lg bg-rose-500/20 text-rose-400 flex items-center justify-center font-bold">
                <TrendingDown className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  SELL Setup Checklist
                  {shortStatus.all_passed && (
                    <span className="px-2 py-0.5 text-xs bg-rose-500 text-slate-950 font-extrabold rounded animate-pulse">
                      SIGNAL ACTIVE
                    </span>
                  )}
                </h3>
                <p className="text-xs text-slate-400">Current Gold Price: <span className="font-mono text-rose-400">${shortStatus.close_price.toFixed(2)}</span></p>
              </div>
            </div>
            <div className="text-right">
              <span className="text-xs text-slate-400">5-Step Status</span>
              <div className="text-sm font-bold text-slate-200">
                {[shortStatus.vwap_pass, shortStatus.crossover_pass, shortStatus.ob_pass, shortStatus.pullback_pass, shortStatus.confirmation_pass].filter(Boolean).length} / 5 Passed
              </div>
            </div>
          </div>

          <div className="space-y-2.5 mt-4">
            {renderConditionRow(
              1,
              "Trend Filter: Close < VWAP",
              shortStatus.vwap_pass,
              shortStatus.vwap_detail,
              `VWAP: $${shortStatus.vwap_value.toFixed(2)}`
            )}

            {renderConditionRow(
              2,
              "Crossover: EMA 9 crosses below EMA 21",
              shortStatus.crossover_pass,
              shortStatus.crossover_detail,
              `EMA9: $${shortStatus.ema_fast.toFixed(2)} | EMA21: $${shortStatus.ema_slow.toFixed(2)}`
            )}

            {renderConditionRow(
              3,
              "Structural: Bearish Order Block Reaction",
              shortStatus.ob_pass,
              shortStatus.ob_detail,
              shortStatus.ob_pass ? "OB Active" : "No OB in Zone"
            )}

            {renderConditionRow(
              4,
              "Pullback: Price retraced near EMAs",
              shortStatus.pullback_pass,
              shortStatus.pullback_detail,
              shortStatus.pullback_pass ? "In Pullback Zone" : "No Retrace"
            )}

            {renderConditionRow(
              5,
              "Confirmation Candle Trigger",
              shortStatus.confirmation_pass,
              shortStatus.confirmation_detail,
              shortStatus.pattern_name || "Waiting Trigger"
            )}
          </div>

          {/* Action Box */}
          <div className="mt-5 pt-4 border-t border-slate-800">
            <div className="grid grid-cols-3 gap-2 text-center text-xs mb-3">
              <div className="bg-slate-800/60 p-2 rounded">
                <span className="text-slate-400 block">Entry Price</span>
                <span className="font-mono font-bold text-slate-200">${shortStatus.suggested_entry.toFixed(2)}</span>
              </div>
              <div className="bg-slate-800/60 p-2 rounded">
                <span className="text-rose-400 block">Stop Loss (-1R)</span>
                <span className="font-mono font-bold text-rose-300">${shortStatus.suggested_sl.toFixed(2)}</span>
                <span className="text-[10px] text-slate-400 block">(${shortStatus.risk_points.toFixed(2)} pts)</span>
              </div>
              <div className="bg-slate-800/60 p-2 rounded">
                <span className="text-emerald-400 block">Take Profit (+2R)</span>
                <span className="font-mono font-bold text-emerald-300">${shortStatus.suggested_tp.toFixed(2)}</span>
                <span className="text-[10px] text-slate-400 block">(${shortStatus.reward_points.toFixed(2)} pts)</span>
              </div>
            </div>

            <button
              onClick={() => onDispatchOrder("SELL", shortStatus.suggested_sl, shortStatus.suggested_tp)}
              className={`w-full py-2.5 px-4 rounded-lg font-bold text-xs flex items-center justify-center space-x-2 transition-all ${
                shortStatus.all_passed
                  ? "bg-rose-500 hover:bg-rose-400 text-slate-950 shadow-md shadow-rose-900/40 cursor-pointer"
                  : "bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700"
              }`}
            >
              <Zap className="w-4 h-4" />
              <span>{shortStatus.all_passed ? "EXECUTE 0.1 LOT DEMO SELL" : "TEST DEMO SELL ORDER"}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
