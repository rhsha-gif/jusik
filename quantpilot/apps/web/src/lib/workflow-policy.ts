export type RiskPreset = "conservative" | "moderate" | "aggressive";

export interface RiskPresetOption {
  value: RiskPreset;
  label: string;
  description: string;
  cashWeight: number;
  maxPositionWeight: number;
  maxPositions: number;
}

export interface ThemeChip {
  theme: string;
  label: string;
  seed: string;
  aliases: string[];
}

export interface SectorChip {
  sector: string;
  label: string;
  aliases: string[];
}

export interface SafeWorkflowPolicyDraft {
  text: string;
  symbols: string[];
  sectors: string[];
  themes: string[];
  riskPreset: RiskPreset;
  riskLabel: string;
}

export const RISK_PRESETS: RiskPresetOption[] = [
  {
    value: "moderate",
    label: "Moderate",
    description: "Balanced mock workflow with weekly rebalance and 20% cash floor.",
    cashWeight: 20,
    maxPositionWeight: 15,
    maxPositions: 8,
  },
  {
    value: "conservative",
    label: "Conservative",
    description: "Lower single-name exposure and a 30% cash floor.",
    cashWeight: 30,
    maxPositionWeight: 10,
    maxPositions: 6,
  },
  {
    value: "aggressive",
    label: "Aggressive",
    description: "Higher research appetite while staying mock, limit-only, and risk-gated.",
    cashWeight: 10,
    maxPositionWeight: 20,
    maxPositions: 10,
  },
];

export const THEME_CHIPS: ThemeChip[] = [
  { theme: "ai", label: "AI", seed: "AI infrastructure", aliases: ["ai", "artificial intelligence", "gpu", "accelerator"] },
  { theme: "semiconductor", label: "Semiconductor", seed: "semiconductor supply chain", aliases: ["semiconductor", "chip", "fabless", "foundry"] },
  { theme: "battery", label: "Battery", seed: "battery and energy storage", aliases: ["battery", "energy storage", "ev"] },
  { theme: "dividend", label: "Dividend", seed: "dividend quality", aliases: ["dividend", "income", "yield"] },
  { theme: "defensive", label: "Defensive", seed: "defensive low volatility", aliases: ["defensive", "low volatility", "stable cash flow"] },
];

export const SECTOR_CHIPS: SectorChip[] = [
  { sector: "technology", label: "Technology", aliases: ["technology", "tech", "software", "cloud"] },
  { sector: "healthcare", label: "Healthcare", aliases: ["healthcare", "health care", "bio", "pharma"] },
  { sector: "financials", label: "Financials", aliases: ["financials", "financial", "finance", "bank", "banks"] },
  { sector: "industrial", label: "Industrial", aliases: ["industrial", "industrials", "automation", "factory"] },
  { sector: "materials", label: "Materials", aliases: ["materials", "material", "chemical", "chemicals"] },
];

const SYMBOL_PATTERN = /\b[A-Z0-9][A-Z0-9.-]{1,12}\b/g;
const IGNORED_SYMBOLS = new Set([
  "AI",
  "AND",
  "API",
  "APPROVAL",
  "BROKER",
  "CASH",
  "CONSERVATIVE",
  "FOCUS",
  "KR",
  "ETF",
  "LIMIT",
  "LIVE",
  "MARKET",
  "MAX",
  "MODERATE",
  "MOCK",
  "NASDAQ",
  "ONLY",
  "ORDER",
  "ORDERS",
  "POSITION",
  "POSITIONS",
  "REBALANCE",
  "REQUIRED",
  "RISK",
  "SECTOR",
  "SECTORS",
  "STOCK",
  "SYMBOL",
  "SYMBOLS",
  "THEME",
  "THEMES",
  "US",
  "WEEKLY",
]);

function riskOption(value: RiskPreset): RiskPresetOption {
  return RISK_PRESETS.find((option) => option.value === value) ?? RISK_PRESETS[0];
}

export function detectThemes(direction: string): string[] {
  const normalized = direction.toLowerCase();
  return THEME_CHIPS.filter((chip) =>
    chip.aliases.some((alias) => normalized.includes(alias.toLowerCase())),
  ).map((chip) => chip.theme);
}

export function detectSectors(direction: string): string[] {
  const normalized = direction.toLowerCase();
  return SECTOR_CHIPS.filter((chip) =>
    chip.aliases.some((alias) => normalized.includes(alias.toLowerCase())),
  ).map((chip) => chip.sector);
}

function looksLikeSymbol(candidate: string): boolean {
  if (IGNORED_SYMBOLS.has(candidate) || candidate.endsWith(".")) return false;
  if (/^\d+$/.test(candidate)) return candidate.length >= 4 && candidate.length <= 8;
  return candidate.length >= 2 && candidate.length <= 6 && /[A-Z]/.test(candidate);
}

export function detectSymbols(direction: string): string[] {
  const matches = direction.toUpperCase().match(SYMBOL_PATTERN) ?? [];
  return Array.from(new Set(matches.filter(looksLikeSymbol))).sort();
}

function marketLabel(direction: string): string {
  const normalized = direction.toLowerCase();
  if (normalized.includes("us") || normalized.includes("nasdaq") || normalized.includes("s&p") || normalized.includes("nyse")) {
    return "US stock";
  }
  return "KR stock";
}

export function buildSafeWorkflowPolicy({
  direction,
  riskPreset,
}: {
  direction: string;
  riskPreset: RiskPreset;
}): SafeWorkflowPolicyDraft {
  const option = riskOption(riskPreset);
  const cleanDirection = direction.trim() || "balanced quality growth";
  const symbols = detectSymbols(cleanDirection);
  const sectors = detectSectors(cleanDirection);
  const themes = detectThemes(cleanDirection);
  const themeText = themes.length > 0 ? `${themes.join(", ")} themes` : "user selected themes";
  const focusLines = [
    symbols.length > 0 ? `Focus symbols: ${symbols.join(", ")}.` : null,
    sectors.length > 0 ? `Focus sectors: ${sectors.join(", ")}.` : null,
    `Focus on ${themeText}.`,
  ].filter((line): line is string => line != null);
  return {
    text: [
      `${marketLabel(cleanDirection)} ${option.value} risk weekly rebalance.`,
      `Investment direction: ${cleanDirection}.`,
      ...focusLines,
      `Cash ${option.cashWeight}%, position ${option.maxPositionWeight}%, max ${option.maxPositions} positions.`,
      "approval required, mock broker, limit orders only.",
      "Live trading disabled; market orders disabled; generate candidate analysis, signals, target weights, rebalance review, risk checks, dry-run output, and operator report only.",
    ].join(" "),
    symbols,
    sectors,
    themes,
    riskPreset: option.value,
    riskLabel: option.label,
  };
}
