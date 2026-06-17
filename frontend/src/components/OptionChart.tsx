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
  const hasSeriesData = option.series.some((serie) => {
    if (serie.data) return serie.data.some((value) => value !== null);
    // dataset-driven series — check if any dataset has non-empty source
    return option.dataset?.some((ds) => {
      const src = ds.source as Array<unknown> | undefined;
      return Array.isArray(src) && src.length > 0;
    }) ?? false;
  });

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
