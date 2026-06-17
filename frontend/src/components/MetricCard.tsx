interface MetricCardProps {
  label: string;
  value: string;
  sub?: string;
  className?: string;
}

export function MetricCard({ label, value, sub, className = "" }: MetricCardProps) {
  return (
    <article className={`metric-card ${className}`.trim()}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{sub || ""}</small>
    </article>
  );
}
