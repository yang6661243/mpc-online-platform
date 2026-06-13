import type { StatusTone } from "../status";

interface StatusBarProps {
  label: string;
  tone: StatusTone;
}

export function StatusBar({ label, tone }: StatusBarProps) {
  return <div className={`status ${tone}`}>{label}</div>;
}
