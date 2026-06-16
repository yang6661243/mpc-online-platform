import { useEffect, useRef } from "react";
import { type DashboardChartOption } from "../chartOptions";
import { init } from "../echartsSetup";

interface OptionChartProps {
  option: DashboardChartOption;
  emptyText: string;
  className?: string;
}

export function OptionChart({ option, emptyText, className = "" }: OptionChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const hasSeriesData = option.series.some((serie) => serie.data.some((value) => value !== null));

  useEffect(() => {
    if (!ref.current || !hasSeriesData) return;
    const chart = init(ref.current);
    chart.setOption(option);
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [hasSeriesData, option]);

  if (!hasSeriesData) {
    return <div className={`empty ${className}`}>{emptyText}</div>;
  }

  return <div ref={ref} className={`chart ${className}`} />;
}
