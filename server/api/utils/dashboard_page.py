from __future__ import annotations

import json


def render_dashboard_page(default_plant_id: str = "aolaide") -> str:
    plant_json = json.dumps(default_plant_id, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MPC 策略对比看板</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #16243d;
      --muted: #60708a;
      --line: #d8e0ea;
      --band: #f5f7fa;
      --surface: #ffffff;
      --actual: #1d6fd4;
      --mpc: #de6b21;
      --battery: #7b4ab0;
      --soc: #198c68;
      --warn: #a94f19;
      --ok: #16764f;
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: #eef2f6;
    }}
    .shell {{ min-height: 100vh; }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      padding: 18px 28px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .meta {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    label {{
      color: var(--muted);
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    input {{
      width: 150px;
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      color: var(--ink);
      background: #fff;
    }}
    button {{
      height: 36px;
      border: 1px solid #bdc8d6;
      border-radius: 6px;
      padding: 0 12px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      cursor: pointer;
    }}
    button:hover {{ background: #f0f4f8; }}
    .content {{
      width: min(1440px, 100%);
      margin: 0 auto;
      padding: 18px 28px 30px;
    }}
    .status {{
      min-height: 28px;
      display: flex;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
    }}
    .status[data-tone="error"] {{ color: #a4362f; }}
    .status[data-tone="ok"] {{ color: var(--ok); }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(150px, 1fr));
      gap: 12px;
      margin: 10px 0 18px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
      min-height: 96px;
    }}
    .card .label {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.3;
    }}
    .card .value {{
      margin-top: 10px;
      font-size: 27px;
      line-height: 1.1;
      font-weight: 700;
      letter-spacing: 0;
      word-break: break-word;
    }}
    .card .sub {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      min-height: 16px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 14px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px 16px;
    }}
    .panel-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }}
    .panel h2 {{
      margin: 0;
      font-size: 17px;
      line-height: 1.25;
      letter-spacing: 0;
    }}
    .legend {{
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
    }}
    .legend span {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }}
    .swatch {{
      width: 22px;
      height: 3px;
      border-radius: 2px;
      background: var(--ink);
    }}
    .swatch.actual {{ background: var(--actual); }}
    .swatch.mpc {{ background: var(--mpc); }}
    .swatch.battery {{ background: var(--battery); }}
    .swatch.soc {{ background: var(--soc); }}
    .chart {{
      width: 100%;
      min-height: 310px;
      background: #fbfcfd;
      border: 1px solid #e6ecf3;
      border-radius: 6px;
      overflow: hidden;
    }}
    svg {{ display: block; width: 100%; height: 310px; }}
    .axis {{ stroke: #c5cfda; stroke-width: 1; }}
    .gridline {{ stroke: #e7edf3; stroke-width: 1; }}
    .tick {{ fill: #66758d; font-size: 11px; }}
    .line {{ fill: none; stroke-width: 2.2; }}
    .line.actual {{ stroke: var(--actual); }}
    .line.mpc {{ stroke: var(--mpc); }}
    .line.battery {{ stroke: var(--battery); }}
    .line.soc {{ stroke: var(--soc); }}
    .empty {{
      min-height: 220px;
      display: grid;
      place-items: center;
      color: var(--muted);
      background: #fbfcfd;
      border: 1px dashed #cfd8e5;
      border-radius: 6px;
    }}
    .table-wrap {{
      overflow: auto;
      border: 1px solid #e6ecf3;
      border-radius: 6px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 720px;
      font-size: 13px;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #edf1f5;
      text-align: right;
      white-space: nowrap;
    }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{
      color: var(--muted);
      background: #f7f9fb;
      font-weight: 600;
    }}

    @media (max-width: 900px) {{
      .topbar {{ align-items: flex-start; flex-direction: column; padding: 16px; }}
      .toolbar {{ width: 100%; justify-content: flex-start; }}
      .content {{ padding: 14px 16px 24px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .card .value {{ font-size: 22px; }}
    }}
    @media (max-width: 560px) {{
      .metrics {{ grid-template-columns: 1fr; }}
      input {{ width: 130px; }}
      .panel {{ padding: 12px; }}
      .panel-head {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <div id="dashboard-root" class="shell">
    <header class="topbar">
      <div>
        <h1>MPC 策略对比看板</h1>
        <div id="subtitle" class="meta">--</div>
      </div>
      <div class="toolbar">
        <label>工厂 <input id="plantInput" autocomplete="off"></label>
        <button id="refreshBtn" type="button">↻ 刷新</button>
      </div>
    </header>
    <main class="content">
      <div id="status" class="status">加载中</div>
      <section id="metrics" class="metrics"></section>
      <section class="grid">
        <div class="panel">
          <div class="panel-head">
            <h2>电网功率对比</h2>
            <div class="legend">
              <span><i class="swatch actual"></i>工厂当前策略</span>
              <span><i class="swatch mpc"></i>MPC 策略</span>
            </div>
          </div>
          <div id="powerChart" class="chart"></div>
        </div>
        <div class="panel">
          <div class="panel-head">
            <h2>储能功率与 SOC</h2>
            <div class="legend">
              <span><i class="swatch battery"></i>储能功率</span>
              <span><i class="swatch soc"></i>SOC</span>
            </div>
          </div>
          <div id="batteryChart" class="chart"></div>
        </div>
        <div class="panel">
          <div class="panel-head">
            <h2>策略明细</h2>
          </div>
          <div id="detailTable" class="table-wrap"></div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const DEFAULT_PLANT_ID = {plant_json};
    const state = {{ data: null }};
    const els = {{
      input: document.getElementById("plantInput"),
      refresh: document.getElementById("refreshBtn"),
      status: document.getElementById("status"),
      subtitle: document.getElementById("subtitle"),
      metrics: document.getElementById("metrics"),
      powerChart: document.getElementById("powerChart"),
      batteryChart: document.getElementById("batteryChart"),
      detailTable: document.getElementById("detailTable"),
    }};

    function fromQuery() {{
      const params = new URLSearchParams(window.location.search);
      return params.get("plant_id") || DEFAULT_PLANT_ID;
    }}

    function numberOrNull(value) {{
      const n = Number(value);
      return Number.isFinite(n) ? n : null;
    }}

    function fmt(value, digits = 1, unit = "") {{
      const n = numberOrNull(value);
      if (n === null) return "--";
      return `${{n.toFixed(digits)}}${{unit}}`;
    }}

    function fmtPct(value) {{
      const n = numberOrNull(value);
      if (n === null) return "--";
      return `${{n.toFixed(1)}}%`;
    }}

    function setStatus(text, tone = "") {{
      els.status.textContent = text;
      els.status.dataset.tone = tone;
    }}

    async function loadDashboard() {{
      const plantId = (els.input.value || DEFAULT_PLANT_ID).trim();
      if (!plantId) return;
      setStatus("加载中");
      try {{
        const res = await fetch(`/api/v1/plants/${{encodeURIComponent(plantId)}}/dashboard`);
        if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
        state.data = await res.json();
        render(state.data);
        setStatus("数据已更新", "ok");
      }} catch (err) {{
        state.data = null;
        renderEmpty();
        setStatus("暂无可展示数据", "error");
      }}
    }}

    function render(data) {{
      const current = data.current || {{}};
      const comparison = data.comparison || {{}};
      els.subtitle.textContent = `${{data.plant_id}} · ${{current.time || "--"}} · ${{current.quality_flag || "--"}}`;
      renderMetrics(current, comparison);
      drawPowerChart(data.series || []);
      drawBatteryChart(data.series || []);
      renderTable(data.series || []);
    }}

    function renderMetrics(current, comparison) {{
      const items = [
        ["当前电网功率", fmt(current.grid_power_kw, 1, " kW"), "防逆流表聚合值"],
        ["当前储能 SOC", fmt(current.soc, 3, ""), "储能表最新窗口"],
        ["工厂最大需量", fmt(comparison.actual_peak_kw, 1, " kW"), "当前策略"],
        ["MPC 最大需量", fmt(comparison.mpc_peak_kw, 1, " kW"), "优化策略"],
        ["削峰量", fmt(comparison.peak_reduction_kw, 1, " kW"), fmtPct(comparison.peak_reduction_pct)],
        ["当前成本", fmt(comparison.actual_cost_yuan, 2, " 元"), "估算"],
        ["MPC 成本", fmt(comparison.mpc_cost_yuan, 2, " 元"), "估算"],
        ["预计节省", fmt(comparison.cost_saving_yuan, 2, " 元"), fmtPct(comparison.cost_saving_pct)],
      ];
      els.metrics.innerHTML = items.map(([label, value, sub]) => `
        <article class="card">
          <div class="label">${{label}}</div>
          <div class="value">${{value}}</div>
          <div class="sub">${{sub || ""}}</div>
        </article>
      `).join("");
    }}

    function renderEmpty() {{
      els.subtitle.textContent = "--";
      els.metrics.innerHTML = "";
      els.powerChart.innerHTML = `<div class="empty">暂无数据</div>`;
      els.batteryChart.innerHTML = `<div class="empty">暂无数据</div>`;
      els.detailTable.innerHTML = `<div class="empty">暂无数据</div>`;
    }}

    function chartPath(values, xScale, yScale) {{
      const pts = values
        .map((v, i) => [numberOrNull(v), i])
        .filter(([v]) => v !== null);
      if (!pts.length) return "";
      return pts.map(([v, i], idx) => `${{idx ? "L" : "M"}} ${{xScale(i)}} ${{yScale(v)}}`).join(" ");
    }}

    function drawChart(target, rows, series, unit) {{
      if (!rows.length) {{
        target.innerHTML = `<div class="empty">暂无曲线数据</div>`;
        return;
      }}
      const width = 1000;
      const height = 310;
      const pad = {{ left: 58, right: 22, top: 22, bottom: 36 }};
      const values = series.flatMap(s => rows.map(r => numberOrNull(r[s.field])).filter(v => v !== null));
      if (!values.length) {{
        target.innerHTML = `<div class="empty">暂无曲线数据</div>`;
        return;
      }}
      let min = Math.min(...values);
      let max = Math.max(...values);
      if (min === max) {{
        min -= 1;
        max += 1;
      }}
      const span = max - min;
      min -= span * 0.08;
      max += span * 0.08;
      const plotW = width - pad.left - pad.right;
      const plotH = height - pad.top - pad.bottom;
      const xScale = i => pad.left + (rows.length === 1 ? 0 : i * plotW / (rows.length - 1));
      const yScale = v => pad.top + (max - v) * plotH / (max - min);
      const ticks = [0, 1, 2, 3, 4].map(i => min + (max - min) * i / 4);
      const yTicks = ticks.map(v => {{
        const y = yScale(v);
        return `<line class="gridline" x1="${{pad.left}}" y1="${{y}}" x2="${{width - pad.right}}" y2="${{y}}"></line>
          <text class="tick" x="${{pad.left - 10}}" y="${{y + 4}}" text-anchor="end">${{v.toFixed(1)}}${{unit}}</text>`;
      }}).join("");
      const paths = series.map(s => `<path class="line ${{s.className}}" d="${{chartPath(rows.map(r => r[s.field]), xScale, yScale)}}"></path>`).join("");
      const firstTime = rows[0]?.time ? rows[0].time.slice(5, 16).replace("T", " ") : "";
      const lastTime = rows[rows.length - 1]?.time ? rows[rows.length - 1].time.slice(5, 16).replace("T", " ") : "";
      target.innerHTML = `
        <svg viewBox="0 0 ${{width}} ${{height}}" preserveAspectRatio="none" role="img">
          ${{yTicks}}
          <line class="axis" x1="${{pad.left}}" y1="${{height - pad.bottom}}" x2="${{width - pad.right}}" y2="${{height - pad.bottom}}"></line>
          <line class="axis" x1="${{pad.left}}" y1="${{pad.top}}" x2="${{pad.left}}" y2="${{height - pad.bottom}}"></line>
          ${{paths}}
          <text class="tick" x="${{pad.left}}" y="${{height - 12}}" text-anchor="start">${{firstTime}}</text>
          <text class="tick" x="${{width - pad.right}}" y="${{height - 12}}" text-anchor="end">${{lastTime}}</text>
        </svg>`;
    }}

    function drawPowerChart(rows) {{
      drawChart(els.powerChart, rows, [
        {{ field: "actual_grid_power_kw", className: "actual" }},
        {{ field: "mpc_grid_power_kw", className: "mpc" }},
      ], "");
    }}

    function drawBatteryChart(rows) {{
      drawChart(els.batteryChart, rows, [
        {{ field: "actual_battery_power_kw", className: "battery" }},
        {{ field: "mpc_battery_power_kw", className: "mpc" }},
        {{ field: "mpc_soc", className: "soc" }},
      ], "");
    }}

    function renderTable(rows) {{
      if (!rows.length) {{
        els.detailTable.innerHTML = `<div class="empty">暂无明细数据</div>`;
        return;
      }}
      const limited = rows.slice(-96);
      els.detailTable.innerHTML = `
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>当前电网 kW</th>
              <th>MPC 电网 kW</th>
              <th>当前储能 kW</th>
              <th>MPC 储能 kW</th>
              <th>当前 SOC</th>
              <th>MPC SOC</th>
              <th>负荷-光伏 kW</th>
            </tr>
          </thead>
          <tbody>
            ${{limited.map(r => `
              <tr>
                <td>${{(r.time || "").replace("T", " ").slice(0, 16)}}</td>
                <td>${{fmt(r.actual_grid_power_kw)}}</td>
                <td>${{fmt(r.mpc_grid_power_kw)}}</td>
                <td>${{fmt(r.actual_battery_power_kw)}}</td>
                <td>${{fmt(r.mpc_battery_power_kw)}}</td>
                <td>${{fmt(r.actual_soc, 3)}}</td>
                <td>${{fmt(r.mpc_soc, 3)}}</td>
                <td>${{fmt(r.load_minus_pv_kw)}}</td>
              </tr>
            `).join("")}}
          </tbody>
        </table>`;
    }}

    els.input.value = fromQuery();
    els.refresh.addEventListener("click", loadDashboard);
    els.input.addEventListener("keydown", event => {{
      if (event.key === "Enter") loadDashboard();
    }});
    renderEmpty();
    loadDashboard();
  </script>
</body>
</html>"""
