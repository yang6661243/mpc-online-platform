const CHINA_TIME_ZONE = "Asia/Shanghai";

function hasExplicitTimeZone(value: string): boolean {
  return /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
}

export function parseDashboardTime(value: string | null | undefined): Date | null {
  if (!value) return null;
  const normalized = hasExplicitTimeZone(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatChinaTime(value: string | null | undefined): string {
  const date = parseDashboardTime(value);
  if (!date) return "--";
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: CHINA_TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.month}-${byType.day} ${byType.hour}:${byType.minute}`;
}

export function formatDataDelay(value: string | null | undefined, now = new Date()): string {
  const date = parseDashboardTime(value);
  if (!date) return "--";
  const minutes = Math.max(0, Math.round((now.getTime() - date.getTime()) / 60000));
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes > 0 ? `${hours}小时${restMinutes}分钟` : `${hours}小时`;
}
