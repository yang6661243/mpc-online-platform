import { formatChinaTime } from "./time";

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return value.toFixed(digits);
}

export function formatKw(value: number | null | undefined): string {
  const text = formatNumber(value, 1);
  return text === "--" ? text : `${text} kW`;
}

export function formatYuan(value: number | null | undefined): string {
  const text = formatNumber(value, 2);
  return text === "--" ? text : `${text} 元`;
}

export function formatPercent(value: number | null | undefined): string {
  const text = formatNumber(value, 1);
  return text === "--" ? text : `${text}%`;
}

export function formatSoc(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  const ratio = value <= 1 ? value * 100 : value;
  return `${ratio.toFixed(1)}%`;
}

export function shortTime(value: string | null | undefined): string {
  return formatChinaTime(value);
}
