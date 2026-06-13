interface MetricCardProps {
  label: string;
  value: string;
  sub?: string;
}

export function MetricCard({ label, value, sub }: MetricCardProps) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{sub || ""}</small>
    </article>
  );
}
