import { useEffect, useRef } from "react";
import { buildRevenueChartOption } from "../chartOptions";
import { init } from "../echartsSetup";
import type { DashboardSeriesPoint } from "../types";

interface RevenueChartProps {
  series: DashboardSeriesPoint[];
}

export function RevenueChart({ series }: RevenueChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = init(ref.current);
    chart.setOption(buildRevenueChartOption(series));
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [series]);

  if (!series.length) {
    return <div className="empty">暂无收益曲线数据</div>;
  }
  return <div ref={ref} className="chart revenue-chart" />;
}
