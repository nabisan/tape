/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Scheme".
 */
export type Scheme = "GICS" | "SIC";
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Venue".
 */
export type Venue = "XNYS" | "FX";
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "VolumeKind".
 */
export type VolumeKind = "traded" | "ticks";
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Json-Output".
 */
export type JsonOutput =
  | boolean
  | number
  | string
  | JsonOutput[]
  | {
      [k: string]: JsonOutput;
    }
  | null;
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Exit".
 */
export type Exit = "signal" | "stop_loss" | "take_profit";
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Timeframe".
 */
export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Json-Input".
 */
export type JsonInput =
  | boolean
  | number
  | string
  | JsonInput[]
  | {
      [k: string]: JsonInput;
    }
  | null;
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "LedgerState".
 */
export type LedgerState = "absent" | "building" | "ready" | "failed";
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "State".
 */
export type State = "running" | "done" | "failed" | "stopped";

export interface TapeApi {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Basket".
 */
export interface Basket {
  industries?: string[];
  notes?: string[];
  quintiles?: string[];
  sectors?: string[];
  tickers: string[];
  universes: Choice[];
  window?: string | null;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Choice".
 */
export interface Choice {
  key: string;
  title: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "BasketQuery".
 */
export interface BasketQuery {
  custom?: string[];
  industries?: string[];
  scheme?: Scheme;
  sectors?: string[];
  start: string;
  universe?: string;
  venue?: Venue;
  /**
   * @maxItems 5
   */
  volatility?: number[];
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Catalog".
 */
export interface Catalog {
  data_version: string;
  first_session: string;
  last_session: string;
  tickers: TickerInfo[];
  venues: VenueInfo[];
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "TickerInfo".
 */
export interface TickerInfo {
  first_session: string;
  last_session: string;
  price_precision: number;
  ticker: string;
  venue: Venue;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "VenueInfo".
 */
export interface VenueInfo {
  data_version: string;
  first_session: string;
  label: string;
  last_session: string;
  periods: number;
  venue: Venue;
  volume: VolumeKind;
  zone: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "ConfigSchema".
 */
export interface ConfigSchema {
  $defs?: {
    [k: string]: FieldSchema;
  };
  properties: {
    [k: string]: FieldSchema;
  };
  [k: string]: unknown;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "FieldSchema".
 */
export interface FieldSchema {
  $ref?: string | null;
  allOf?: FieldSchema[] | null;
  const?: JsonOutput;
  default?: JsonOutput;
  enum?: JsonOutput[] | null;
  exclusiveMaximum?: number | null;
  exclusiveMinimum?: number | null;
  maximum?: number | null;
  minimum?: number | null;
  step?: number | null;
  title?: string | null;
  type?: string | null;
  when?: {
    [k: string]: JsonOutput;
  } | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "ExecutionChoice".
 */
export interface ExecutionChoice {
  commission?: boolean;
  commission_per_share?: number;
  entry_delay_minutes?: number;
  intraday?: boolean;
  prob_fill_on_limit?: number;
  slippage?: boolean;
  slippage_bps?: number;
  squareoff_minutes_before_close?: number;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "FormModel".
 */
export interface FormModel {
  api: string;
  busy: boolean;
  message: string;
  page: "run" | "simulation";
  pages: {
    [k: string]: string;
  };
  seed: Seed | null;
  submission: RunRequestOutput | SweepRequestOutput | null;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Seed".
 */
export interface Seed {
  compare?: boolean;
  end?: string | null;
  execution?: ExecutionChoice;
  legs?: {
    [k: string]: LegOutput;
  };
  start?: string | null;
  ticker?: string | null;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Leg-Output".
 */
export interface LegOutput {
  params?: {
    [k: string]: JsonOutput;
  };
  strategy: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "RunRequest-Output".
 */
export interface RunRequestOutput {
  end: string;
  execution?: ExecutionChoice;
  legs: {
    [k: string]: LegOutput;
  };
  n: number;
  start: string;
  ticker: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "SweepRequest-Output".
 */
export interface SweepRequestOutput {
  end: string;
  execution?: ExecutionChoice;
  grid?: {
    [k: string]: number[];
  };
  n: number;
  params?: {
    [k: string]: JsonOutput;
  };
  start: string;
  strategy: string;
  /**
   * @minItems 1
   */
  tickers: [string, ...string[]];
  timeframe: Timeframe;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "LedgerModel".
 */
export interface LedgerModel {
  api: string;
  key: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "LedgerStatus".
 */
export interface LedgerStatus {
  detail?: string;
  size?: number | null;
  state: LedgerState;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Leg-Input".
 */
export interface LegInput {
  params?: {
    [k: string]: JsonInput;
  };
  strategy: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Ordered".
 */
export interface Ordered {
  lower: string;
  strict?: boolean;
  upper: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "PathsFigure".
 */
export interface PathsFigure {
  config: {
    [k: string]: unknown;
  };
  layout: {
    [k: string]: unknown;
  };
  traces: PathsTrace[];
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "PathsTrace".
 */
export interface PathsTrace {
  meta: string;
  name?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "PathsMeta".
 */
export interface PathsMeta {
  dates: string[];
  series: string[];
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "PathsModel".
 */
export interface PathsModel {
  api: string;
  figure: PathsFigure;
  key: string;
  plotly: string;
  runs: string[];
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "ResolvedSpace".
 */
export interface ResolvedSpace {
  choices: {
    [k: string]: {
      [k: string]: number[];
    };
  };
  on_demand?: Timeframe[];
  ordered: Ordered[];
  ready?: {
    [k: string]: {
      [k: string]: {
        [k: string]: string;
      };
    };
  };
  timeframes: Timeframe[];
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "RunRequest-Input".
 */
export interface RunRequestInput {
  end: string;
  execution?: ExecutionChoice;
  legs: {
    [k: string]: LegInput;
  };
  n: number;
  start: string;
  ticker: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "Skipped".
 */
export interface Skipped {
  reason: string;
  ticker: string;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "StrategyInfo".
 */
export interface StrategyInfo {
  config_schema: {
    [k: string]: unknown;
  };
  exits: Exit[];
  intraday: boolean;
  title: string;
  version: number;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "StrategySpace".
 */
export interface StrategySpace {
  config_schema: {
    [k: string]: unknown;
  };
  name: string;
  space: ResolvedSpace;
  ticker: string;
  version: number;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "SweepRequest-Input".
 */
export interface SweepRequestInput {
  end: string;
  execution?: ExecutionChoice;
  grid?: {
    [k: string]: number[];
  };
  n: number;
  params?: {
    [k: string]: JsonInput;
  };
  start: string;
  strategy: string;
  /**
   * @minItems 1
   */
  tickers: [string, ...string[]];
  timeframe: Timeframe;
}
/**
 * This interface was referenced by `TapeApi`'s JSON-Schema
 * via the `definition` "SweepStatus".
 */
export interface SweepStatus {
  done: number;
  error?: string | null;
  failed: number;
  key: string;
  skipped: Skipped[];
  state: State;
  strategy?: string;
  tickers?: number;
  timeframe?: string;
  total: number;
}
