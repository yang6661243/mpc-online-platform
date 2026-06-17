import { useEffect, useRef } from "react";
import { buildPowerChartOption } from "../chartOptions";
import { init } from "../echartsSetup";
import type { DashboardSeriesPoint } from "../types";

interface PowerChartProps {
  series: DashboardSeriesPoint[];
}

export function PowerChart({ series }: PowerChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = init(ref.current);
    chart.setOption(buildPowerChartOption(series));
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
