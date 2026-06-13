import { formatKw, formatSoc, shortTime } from "../format";
import type { DashboardSeriesPoint } from "../types";

interface DetailTableProps {
  series: DashboardSeriesPoint[];
}

export function DetailTable({ series }: DetailTableProps) {
  if (!series.length) {
    return <div className="empty">暂无明细数据</div>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>实际电网</th>
            <th>MPC 电网</th>
            <th>实际储能</th>
            <th>MPC 储能</th>
            <th>实际 SOC</th>
            <th>MPC SOC</th>
            <th>质量</th>
          </tr>
        </thead>
        <tbody>
          {series.map((point) => (
            <tr key={point.time}>
              <td>{shortTime(point.time)}</td>
              <td>{formatKw(point.actual_grid_power_kw)}</td>
              <td>{formatKw(point.mpc_grid_power_kw)}</td>
              <td>{formatKw(point.actual_battery_power_kw)}</td>
              <td>{formatKw(point.mpc_battery_power_kw)}</td>
              <td>{formatSoc(point.actual_soc)}</td>
              <td>{formatSoc(point.mpc_soc)}</td>
              <td>{point.quality_flag}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
