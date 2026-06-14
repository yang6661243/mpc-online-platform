import { useEffect, useRef } from "react";
import { buildStrategyChartOption, type StrategyKind } from "../chartOptions";
import { formatKw, formatSoc, formatYuan } from "../format";
import { init } from "../echartsSetup";
import { STRATEGY_CHART_HEIGHT_PX } from "../layout";
import type { DashboardSeriesPoint } from "../types";

interface StrategyCardProps {
  title: string;
  subtitle: string;
  strategy: StrategyKind;
  series: DashboardSeriesPoint[];
  peakKw: number | null | undefined;
  costYuan: number | null | undefined;
  soc: number | null | undefined;
  batteryKw: number | null | undefined;
  badge: string;
  savingYuan?: number | null | undefined;
}

export function StrategyCard({
  title,
  subtitle,
  strategy,
  series,
  peakKw,
  costYuan,
  soc,
  batteryKw,
  badge,
  savingYuan,
}: StrategyCardProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current || !series.length) return;
    const chart = init(ref.current);
    chart.setOption(buildStrategyChartOption(series, strategy));
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [series, strategy]);

  return (
    <article className={`strategy-card strategy-card-${strategy}`}>
      <div className="strategy-head">
        <div>
          <span className="strategy-badge">{badge}</span>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        {savingYuan !== undefined && (
          <div className="saving-pill">
            <span>预计节省</span>
            <strong>{formatYuan(savingYuan)}</strong>
          </div>
        )}
      </div>

      <div className="strategy-kpis">
        <div>
          <span>最大需量</span>
          <strong>{formatKw(peakKw)}</strong>
        </div>
        <div>
          <span>运行成本</span>
          <strong>{formatYuan(costYuan)}</strong>
        </div>
        <div>
          <span>当前 SOC</span>
          <strong>{formatSoc(soc)}</strong>
        </div>
        <div>
          <span>储能功率</span>
          <strong>{formatKw(batteryKw)}</strong>
        </div>
      </div>

      {series.length ? (
        <div ref={ref} className="chart strategy-chart" style={{ height: STRATEGY_CHART_HEIGHT_PX }} />
      ) : (
        <div className="empty">暂无策略曲线数据</div>
      )}
    </article>
  );
}
