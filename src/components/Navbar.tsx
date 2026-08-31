import React from "react";
import { Activity, ShieldCheck, ShieldAlert, Terminal, RefreshCw, PlayCircle, CheckCircle2 } from "lucide-react";
import { AccountData } from "../types";

interface NavbarProps {
  account: AccountData | null;
  activeTab: string;
  setActiveTab: (tab: string) => void;
  noiseGatePassed: boolean;
  noisePValue: number;
  onRefresh: () => void;
  isLoading: boolean;
}

export const Navbar: React.FC<NavbarProps> = ({
  account,
  activeTab,
  setActiveTab,
  noiseGatePassed,
  noisePValue,
  onRefresh,
  isLoading
}) => {
  const tabs = [
    { id: "checklist", label: "Live Checklist & Signals" },
    { id: "chart", label: "M1 Live Chart & OBs" },
    { id: "backtest", label: "Causal Backtest & Noise Gate" },
    { id: "safety", label: "Safety & Circuit Breakers" },
    { id: "tests", label: "Unit Tests (14 Tests)" },
    { id: "docs", label: "Strategy Specs & Rules" },
  ];

  return (
    <header className="bg-slate-900 border-b border-slate-800 text-slate-100 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Logo & Symbol info */}
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-lg bg-amber-500/20 border border-amber-500/30 flex items-center justify-center text-amber-400 font-bold text-lg">
              Au
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-bold text-base tracking-wide text-white">XAU/USD 1M Scalper</span>
                <span className="px-2 py-0.5 text-xs font-semibold rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
                  Triple Filter EMA 9/21 + VWAP
                </span>
              </div>
              <p className="text-xs text-slate-400">MetaTrader 5 &bull; Causal Order Blocks &bull; Monte Carlo Gate</p>
            </div>
          </div>

          {/* Account & Safety Badges */}
          <div className="hidden md:flex items-center space-x-3 text-xs">
            {/* Demo Status */}
            <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded-md bg-emerald-950/80 border border-emerald-800/80 text-emerald-300">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              <span className="font-semibold">DEMO MODE ACTIVE</span>
            </div>

            {/* Noise Gate Status Badge */}
            <div className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-md border ${
              noiseGatePassed
                ? "bg-emerald-950/80 border-emerald-700 text-emerald-300"
                : "bg-rose-950/80 border-rose-800 text-rose-300"
            }`}>
              {noiseGatePassed ? (
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
              )}
              <span>
                Noise Gate: <strong className="font-bold">{noiseGatePassed ? "PASSED" : "FAILED"}</strong> (p={noisePValue.toFixed(4)})
              </span>
            </div>

            {/* Live Gold Spread */}
            {account && (
              <div className="px-2.5 py-1 rounded-md bg-slate-800 border border-slate-700 text-slate-300">
                Gold: <strong className="text-amber-400">${account.bid.toFixed(2)}</strong> / <strong className="text-amber-400">${account.ask.toFixed(2)}</strong>
                <span className="ml-1 text-slate-400">(${account.spread_usd.toFixed(2)})</span>
              </div>
            )}

            <button
              onClick={onRefresh}
              disabled={isLoading}
              className="p-1.5 rounded-md bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition-colors disabled:opacity-50"
              title="Refresh Market Data"
            >
              <RefreshCw className={`w-4 h-4 ${isLoading ? "animate-spin text-amber-400" : ""}`} />
            </button>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="flex space-x-1 overflow-x-auto scrollbar-none py-1 border-t border-slate-800">
          {tabs.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-3.5 py-2 text-xs font-medium rounded-md whitespace-nowrap transition-all ${
                  isActive
                    ? "bg-amber-500 text-slate-950 font-semibold shadow-sm"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>
    </header>
  );
};
