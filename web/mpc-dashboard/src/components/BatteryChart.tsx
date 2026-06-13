import { useEffect, useRef } from "react";
import { buildBatteryChartOption } from "../chartOptions";
import { init } from "../echartsSetup";
import type { DashboardSeriesPoint } from "../types";

interface BatteryChartProps {
  series: DashboardSeriesPoint[];
}

export function BatteryChart({ series }: BatteryChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = init(ref.current);
    chart.setOption(buildBatteryChartOption(series));
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [series]);

  if (!series.length) {
    return <div className="empty">暂无曲线数据</div>;
  }
  return <div ref={ref} className="chart" />;
}
