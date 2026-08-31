export interface BarData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema9: number;
  ema21: number;
  vwap: number;
  atr: number;
}

export interface OrderBlockData {
  id: string;
  direction: "BULLISH" | "BEARISH";
  bar_index: number;
  zone_low: number;
  zone_high: number;
  is_active: boolean;
  is_mitigated: boolean;
}

export interface ChecklistCondition {
  close_price: number;
  vwap_value: number;
  vwap_pass: boolean;
  vwap_detail: string;
  ema_fast: number;
  ema_slow: number;
  crossover_pass: boolean;
  crossover_detail: string;
  ob_pass: boolean;
  ob_detail: string;
  pullback_pass: boolean;
  pullback_detail: string;
  confirmation_pass: boolean;
  pattern_name: string;
  confirmation_detail: string;
  all_passed: boolean;
  signal: "BUY" | "SELL" | null;
  suggested_entry: number;
  suggested_sl: number;
  suggested_tp: number;
  risk_points: number;
  reward_points: number;
}

export interface AccountData {
  mode: string;
  is_demo: boolean;
  balance: number;
  equity: number;
  bid: number;
  ask: number;
  spread_usd: number;
  algo_trading: boolean;
}

export interface MarketResponse {
  bars: BarData[];
  order_blocks: OrderBlockData[];
  checklist: {
    LONG: ChecklistCondition;
    SHORT: ChecklistCondition;
  };
  account: AccountData;
}

export interface BacktestMetricsData {
  segment_name: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  profit_factor: number;
  expectancy_r: number;
  expectancy_usd: number;
  total_net_pnl_usd: number;
  max_drawdown_usd: number;
  max_drawdown_pct: number;
  average_trade_bars: number;
  avg_win_usd: number;
  avg_loss_usd: number;
  payoff_ratio: number;
  max_consecutive_losses: number;
  sharpe_ratio: number;
  noise_gate_passed: boolean;
  noise_p_value: number;
  z_score: number;
  shuffled_expectancies: number[];
}

export interface TradeRecord {
  id: number;
  direction: "BUY" | "SELL";
  signal_bar: number;
  signal_time: string;
  entry_bar: number;
  entry_time: string;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  exit_bar: number;
  exit_time: string;
  exit_price: number;
  exit_reason: string;
  net_pnl_usd: number;
  pnl_r_multiple: number;
  duration_bars: number;
  pattern_name: string;
  is_out_of_sample: boolean;
}

export interface BacktestResultData {
  in_sample: BacktestMetricsData;
  out_of_sample: BacktestMetricsData;
  overall: BacktestMetricsData;
  trades: TradeRecord[];
  equity_curve: Array<{
    bar_index: number;
    time: string;
    balance: number;
    equity: number;
    is_out_of_sample: boolean;
  }>;
  split_index: number;
  initial_balance: number;
  final_balance: number;
}
