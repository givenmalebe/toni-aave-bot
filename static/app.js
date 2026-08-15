/* TONI // BOT CONTROL - live dashboard client. Broadcast + user-initiated funding. */
(() => {
  "use strict";

  const SPONSOR = "0xfcc8598e8297d86cd3a1595213deaee50e56a265";
  const RESERVE_SYMS = ["WETH", "USDC", "USDT", "GHO", "GDOLLAR", "FRAX", "EURC", "RLUSD",
                        "cbBTC", "WBTC", "AAVE", "LINK", "wstETH", "weETH"];
  const $ = (id) => document.getElementById(id);

  const fmt = {
    num: (n, d = 2) => (n == null || isNaN(n) ? "--" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d })),
    usd: (n, d = 2) => (n == null || isNaN(n) ? "--" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: d })),
    eth: (n, d = 4) => (n == null || isNaN(n) ? "--" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d }) + " ETH"),
    age: (ts) => {
      if (!ts) return "--";
      const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
      if (s < 60) return s + "s";
      if (s < 3600) return Math.floor(s / 60) + "m";
      if (s < 86400) return Math.floor(s / 3600) + "h";
      return Math.floor(s / 86400) + "d";
    },
    ts: (ts) => new Date((ts * 1000) + SAST_OFFSET * 1000).toISOString().slice(11, 19),
  };

  /* SAST = UTC+2, no DST. Client-side shift so every timestamp is South African time. */
  const SAST_OFFSET = 2 * 3600;
  const tickSast = () => {
    const el = $("p-sast");
    if (el) el.textContent = new Date(Date.now() + SAST_OFFSET * 1000).toISOString().slice(11, 19);
  };

  /* ------------------------------------------------ charts */
  const palette = ["#22d3ee", "#a78bfa", "#f59e0b", "#22c55e", "#ef4444", "#3b82f6", "#ec4899", "#14b8a6"];
  const mkChart = (id, cfg) => {
    const el = $(id);
    if (!el) return null;
    return new Chart(el, cfg);
  };
  const lineOpts = (color, fill = true) => ({
    type: "line",
    data: { labels: [], datasets: [{ data: [], borderColor: color, backgroundColor: fill ? color + "22" : "transparent", pointRadius: 0, borderWidth: 2, tension: .3 }] },
    options: { responsive: true, animation: false, plugins: { legend: { display: false } },
      scales: { x: { display: false }, y: { display: true, ticks: { font: { size: 9 }, color: "#64748b" }, grid: { color: "#1e293b55" } } } },
  });

  const chartTx = mkChart("chart-tx", {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "pending", data: [], borderColor: "#22d3ee", backgroundColor: "#22d3ee22",
          pointRadius: 0, borderWidth: 2, tension: .35, fill: true, yAxisID: "y" },
        { label: "queued", data: [], borderColor: "#64748b", backgroundColor: "transparent",
          pointRadius: 0, borderWidth: 1.5, tension: .35, borderDash: [4, 3], yAxisID: "y" },
        { label: "MEV", data: [], borderColor: "#f59e0b", backgroundColor: "#f59e0b33",
          pointRadius: 0, borderWidth: 2, tension: .35, fill: true, yAxisID: "y1" },
      ],
    },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => items.length ? items[0].label : "",
          },
        },
      },
      scales: {
        x: { display: false },
        y: {
          position: "left",
          ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" },
        },
        y1: {
          position: "right",
          ticks: { font: { size: 8 }, color: "#f59e0b", maxTicksLimit: 3 },
          grid: { drawOnChartArea: false },
        },
      },
    },
  });
  const chartComp = mkChart("chart-comp", {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "1h liqs", data: [], borderColor: "#ef4444", backgroundColor: "#ef444433",
          pointRadius: 0, borderWidth: 2, tension: .35, fill: true },
        { label: "missed", data: [], borderColor: "#f59e0b", backgroundColor: "transparent",
          pointRadius: 0, borderWidth: 1.5, tension: .35, borderDash: [4, 3] },
      ],
    },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: {
          beginAtZero: true,
          ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" },
        },
      },
    },
  });
  const chartArb = mkChart("chart-arb", {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "best net", data: [], borderColor: "#22c55e", backgroundColor: "#22c55e33",
          pointRadius: 0, borderWidth: 2, tension: .35, fill: true, yAxisID: "y" },
        { label: "actionable", data: [], borderColor: "#22d3ee", backgroundColor: "transparent",
          pointRadius: 0, borderWidth: 1.5, tension: .35, yAxisID: "y1" },
      ],
    },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: {
          ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" },
        },
        y1: {
          position: "right",
          beginAtZero: true,
          ticks: { font: { size: 8 }, color: "#22d3ee", maxTicksLimit: 3 },
          grid: { drawOnChartArea: false },
        },
      },
    },
  });
  const chartHours = mkChart("chart-hours", {
    type: "bar",
    data: {
      labels: Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0")),
      datasets: [{
        data: Array(24).fill(0),
        backgroundColor: Array(24).fill("#22d3ee66"),
        borderColor: "#22d3ee",
        borderWidth: 1,
        borderRadius: 2,
      }],
    },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: { size: 8 }, color: "#64748b", maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
          grid: { color: "#1e293b55" } },
        y: { beginAtZero: true, ticks: { font: { size: 9 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" } },
      },
    },
  });
  const chartDows = mkChart("chart-dows", {
    type: "bar",
    data: {
      labels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
      datasets: [{
        data: [0, 0, 0, 0, 0, 0, 0],
        backgroundColor: Array(7).fill("#a78bfa66"),
        borderColor: "#a78bfa",
        borderWidth: 1,
        borderRadius: 2,
      }],
    },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: { size: 9 }, color: "#64748b" }, grid: { color: "#1e293b55" } },
        y: { beginAtZero: true, ticks: { font: { size: 9 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" } },
      },
    },
  });
  const gauge = mkChart("gauge", {
    type: "doughnut",
    data: { datasets: [{ data: [0, 100], backgroundColor: ["#22c55e", "#1e293b"], borderWidth: 0 }] },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      cutout: "78%", plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
  const chartIntelTrend = mkChart("chart-intel-trend", {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        data: [], borderColor: "#22d3ee", backgroundColor: "#22d3ee22",
        pointRadius: 0, borderWidth: 2, tension: .35, fill: true,
      }],
    },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: {
          min: 0, max: 1,
          ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 3,
            callback: (v) => Math.round(v * 100) + "%" },
          grid: { color: "#1e293b55" },
        },
      },
    },
  });
  const intelTrendHist = [];

  /* ======================== SOL twin renderers ======================== */
  const solChartTx = mkChart("sol-chart-tx", {
    type: "line",
    data: { labels: [], datasets: [
      { label: "median", data: [], borderColor: "#22d3ee", backgroundColor: "#22d3ee22",
        pointRadius: 0, borderWidth: 2, tension: .35, fill: true, yAxisID: "y" },
      { label: "p90", data: [], borderColor: "#a78bfa", backgroundColor: "transparent",
        pointRadius: 0, borderWidth: 1.5, tension: .35, yAxisID: "y" },
      { label: "TPS", data: [], borderColor: "#f59e0b", backgroundColor: "#f59e0b22",
        pointRadius: 0, borderWidth: 2, tension: .35, fill: true, yAxisID: "y1" },
    ]},
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 4 }, grid: { color: "#1e293b55" } },
        y1: { position: "right", beginAtZero: true,
          ticks: { font: { size: 8 }, color: "#f59e0b", maxTicksLimit: 3 },
          grid: { drawOnChartArea: false } },
      },
    },
  });
  const solChartComp = mkChart("sol-chart-comp", {
    type: "line",
    data: { labels: [], datasets: [
      { label: "sigs", data: [], borderColor: "#a78bfa", backgroundColor: "#a78bfa33",
        pointRadius: 0, borderWidth: 2, tension: .35, fill: true },
      { label: "reverts", data: [], borderColor: "#f59e0b", backgroundColor: "transparent",
        pointRadius: 0, borderWidth: 1.5, tension: .35, borderDash: [4, 3] },
    ]},
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { beginAtZero: true, ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" } },
      },
    },
  });
  const solChartArb = mkChart("sol-chart-arb", {
    type: "line",
    data: { labels: [], datasets: [
      { label: "best net", data: [], borderColor: "#22c55e", backgroundColor: "#22c55e33",
        pointRadius: 0, borderWidth: 2, tension: .35, fill: true, yAxisID: "y" },
      { label: "actionable", data: [], borderColor: "#a78bfa", backgroundColor: "transparent",
        pointRadius: 0, borderWidth: 1.5, tension: .35, yAxisID: "y1" },
    ]},
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 4 }, grid: { color: "#1e293b55" } },
        y1: { position: "right", beginAtZero: true,
          ticks: { font: { size: 8 }, color: "#a78bfa", maxTicksLimit: 3 },
          grid: { drawOnChartArea: false } },
      },
    },
  });
  const solChartHours = mkChart("sol-chart-hours", {
    type: "bar",
    data: {
      labels: Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0")),
      datasets: [{ data: Array(24).fill(0), backgroundColor: Array(24).fill("#a78bfa66"),
        borderColor: "#a78bfa", borderWidth: 1, borderRadius: 2 }],
    },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: { size: 8 }, color: "#64748b", maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
          grid: { color: "#1e293b55" } },
        y: { beginAtZero: true, ticks: { font: { size: 9 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" } },
      },
    },
  });
  const solChartDows = mkChart("sol-chart-dows", {
    type: "bar",
    data: {
      labels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
      datasets: [{ data: [0, 0, 0, 0, 0, 0, 0], backgroundColor: Array(7).fill("#c084fc66"),
        borderColor: "#c084fc", borderWidth: 1, borderRadius: 2 }],
    },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: { size: 9 }, color: "#64748b" }, grid: { color: "#1e293b55" } },
        y: { beginAtZero: true, ticks: { font: { size: 9 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" } },
      },
    },
  });
  const solGauge = mkChart("sol-gauge", {
    type: "doughnut",
    data: { datasets: [{ data: [0, 100], backgroundColor: ["#a78bfa", "#1e293b"], borderWidth: 0 }] },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      cutout: "78%", plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
  const solChartIntelTrend = mkChart("sol-chart-intel-trend", {
    type: "line",
    data: { labels: [], datasets: [{
      data: [], borderColor: "#a78bfa", backgroundColor: "#a78bfa22",
      pointRadius: 0, borderWidth: 2, tension: .35, fill: true,
    }]},
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { min: 0, max: 1,
          ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 3,
            callback: (v) => Math.round(v * 100) + "%" },
          grid: { color: "#1e293b55" } },
      },
    },
  });
  const solIntelTrendHist = [];
  let solMpFilter = "all", solOpFilter = "all", solArFilter = "all", solBcFilter = "all", solCpFilter = "all";
  let solMpLiveCache = [], solOpCache = [], solArCache = [], solBcRowsCache = [], solCpCache = [];
  let solAlFilter = "all", solAlCat = "all", solAlSearch = "", solAlAutoscroll = true;
  let solLogLines = [];

  const updateSolHeader = (s) => {
    const sol = s.sol || {};
    const lblB = $("p-block-lbl"), lblG = $("p-gas-lbl"), lblE = $("p-eth-lbl");
    if (lblB) lblB.textContent = "slot";
    if (lblG) lblG.textContent = "prio";
    if (lblE) lblE.textContent = "SOL";
    $("p-block").textContent = sol.slot != null ? fmt.num(sol.slot, 0) : "--";
    const fee = sol.priority_fee;
    $("p-gas").textContent = fee != null ? fmt.num(fee, 0) + " µl" : "--";
    $("p-gas").style.color = (fee || 0) > 50000 ? "var(--red)" : (fee || 0) > 5000 ? "var(--amber)" : "var(--green)";
    $("p-eth").textContent = sol.sol_price_usd != null ? fmt.usd(sol.sol_price_usd) : "--";
    const intel = sol.intel || {};
    $("p-ready").textContent = (intel.readiness != null ? intel.readiness : 0) + "%";
    const bc = sol.broadcast || {};
    const ready = bc.ready || {};
    const bcastEl = $("p-bcast");
    if (bcastEl) {
      if (!bc.enabled) bcastEl.textContent = "off";
      else if (ready.liq || ready.arb) bcastEl.textContent =
        (ready.liq ? "liq" : "") + (ready.liq && ready.arb ? "+" : "") + (ready.arb ? "arb" : "");
      else bcastEl.textContent = "blocked";
      bcastEl.style.color = (!bc.enabled ? "var(--amber)"
        : (ready.liq || ready.arb) ? "var(--green)" : "var(--red)");
    }
  };

  const updateSolBots = (sol) => {
    const el = $("sol-bots-list");
    if (!el) return;
    const labels = {
      mempool: "Priority Fee Watcher", prices: "Slot / SOL Price", funds: "SOL Funds",
      sweep: "Solend Opportunity Sweep", competitors: "Solend Program Watch",
      arb: "Jupiter Arb Scanner", intel: "SOL Learning / Intel", broadcast: "SOL Broadcast",
    };
    el.innerHTML = Object.entries(labels).map(([k, name]) => {
      const b = (sol.bots || {})[k] || {};
      const age = b.last ? fmt.age(b.last) + " ago" : "never";
      return `<div class="bot">
        <span class="st ${b.status || "idle"}"></span>
        <div><div class="b-name">${name}</div>
          <div class="b-last">${age}</div>
          <div class="b-msg">${b.msg || ""}</div></div></div>`;
    }).join("");
  };

  const updateSolFunds = (sol) => {
    const funds = sol.funds || {};
    const wallets = sol.wallets || {};
    const perf = sol.performance || {};
    const set = (id, v) => { const e = $(id); if (e) e.textContent = v; };
    set("sol-fp-grade", perf.grade || "—");
    set("sol-fp-verdict", perf.verdict || "—");
    set("sol-fp-equity", perf.equity_usd != null ? fmt.usd(perf.equity_usd) : "--");
    set("sol-fp-pnl", perf.session_pnl != null ? fmt.usd(perf.session_pnl) : "--");
    set("sol-fp-realized", perf.realized != null ? fmt.usd(perf.realized) : "--");
    set("sol-fp-sim", perf.sim != null ? fmt.usd(perf.sim) : "--");
    set("sol-fp-hit", perf.hit_rate != null ? fmt.num(perf.hit_rate, 1) + "%" : "--");
    set("sol-fp-missed", perf.missed != null ? fmt.num(perf.missed, 0) : "--");
    const edge = $("sol-fp-edge");
    if (edge) edge.textContent = perf.edge || sol.protocol_note || "";
    const ledger = $("sol-fp-ledger");
    if (ledger) {
      const rows = perf.ledger || [];
      ledger.innerHTML = rows.length
        ? rows.map((r) => `<tr><td>${fmt.ts(r.ts)}</td><td>${r.kind || ""}</td><td>${r.stage || ""}</td><td>${fmt.usd(r.usd)}</td></tr>`).join("")
        : `<tr><td colspan="4" class="dim">no SOL broadcasts yet</td></tr>`;
    }
    const table = $("sol-funds-table");
    if (table) {
      const names = ["funder", "sponsor", "bot"];
      const tags = { funder: "capital", sponsor: "tips", bot: "fee payer" };
      table.innerHTML = `<table class="mini"><thead><tr><th>wallet</th><th>SOL</th><th>target</th><th>pubkey</th></tr></thead><tbody>` +
        names.map((n) => {
          const f = funds[n] || {};
          const pk = f.pubkey || wallets[n] || "";
          const short = pk ? `${pk.slice(0, 4)}…${pk.slice(-4)}` : "unset";
          const amt = f.configured
            ? (f.sol != null
                ? `<b style="color:${f.sol > 0 ? "var(--green)" : "var(--amber)"}">${fmt.num(f.sol, 4)}</b>`
                : "err")
            : "—";
          const tgt = f.target_sol != null
            ? `<span class="${(f.shortfall_sol || 0) > 0 ? "amber" : "dim"}">${fmt.num(f.target_sol, 2)}</span>`
            : `<span class="dim">—</span>`;
          const addr = pk
            ? `<span class="mono copy" data-addr="${pk}" title="click to copy">${short}</span> <a href="https://solscan.io/account/${pk}" target="_blank" rel="noopener" style="color:var(--violet)">↗</a>`
            : `<span class="mono dim">unset</span>`;
          return `<tr><td>${n} <span class="tag sol-tag">${tags[n] || f.role || ""}</span></td><td>${amt}</td><td>${tgt}</td><td>${addr}</td></tr>`;
        }).join("") + `</tbody></table>`;
    }
    const check = $("sol-fund-checklist");
    if (check) {
      const g = sol.fund_guide || {};
      const sp = (funds.sponsor || {}).pubkey || g.sponsor || wallets.sponsor || "";
      const bt = (funds.bot || {}).pubkey || g.bot || wallets.bot || "";
      const fd = (funds.funder || {}).pubkey || g.from_pubkey || wallets.funder || "";
      const ts = g.sponsor_target_sol != null ? g.sponsor_target_sol : 0.08;
      const tb = g.bot_target_sol != null ? g.bot_target_sol : 0.25;
      const row = (label, amt, pk, note) => {
        const short = pk ? `${pk.slice(0, 4)}…${pk.slice(-4)}` : "unset";
        return `<div class="sol-fund-row"><span class="dim">${label}</span><b>${amt}</b>` +
          (pk
            ? `<span class="mono copy" data-addr="${pk}" title="${pk}">${short}</span>`
            : `<span class="mono dim">unset</span>`) +
          `<span class="dim">${note}</span></div>`;
      };
      check.innerHTML =
        `<div class="sol-fund-h">send from funder <span class="mono copy" data-addr="${fd}">${fd ? fd.slice(0,4)+"…"+fd.slice(-4) : "—"}</span></div>` +
        row("sponsor", ts + " SOL", sp, "Jito + prio") +
        row("bot", tb + " SOL", bt, "CU + inventory");
    }
  };

  const fmtUl = (n) => {
    if (n == null || Number.isNaN(+n)) return "--";
    const v = +n;
    if (v >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (v >= 1000) return (v / 1000).toFixed(v >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k";
    return String(Math.round(v));
  };
  const SOL_FEE_COLORS = {
    hot: "#ef4444", elevated: "#f59e0b", busy: "#22d3ee",
    quiet: "#94a3b8", zero: "#334155",
  };
  const SOL_FEE_ORDER = ["hot", "elevated", "busy", "quiet", "zero"];

  const renderSolMpLive = () => {
    const body = $("sol-mp-mev-live") && $("sol-mp-mev-live").querySelector("tbody");
    const empty = $("sol-mp-mev-empty");
    const note = $("sol-mp-mev-note");
    if (!body) return;
    const live = solMpFilter === "all" ? solMpLiveCache
      : solMpLiveCache.filter((t) => t.cls === solMpFilter);
    if (note) note.textContent = `${live.length}/${solMpLiveCache.length} · fee-sorted`;
    if (empty) empty.style.display = live.length ? "none" : "block";
    body.innerHTML = live.slice(0, 50).map((t) => {
      const slot = t.slot != null ? String(t.slot) : "--";
      const link = t.solscan
        ? `<a href="${t.solscan}" target="_blank" rel="noopener" style="color:var(--violet)">${slot.slice(-8)}</a>`
        : `<span class="mono dim">${slot.slice(-8)}</span>`;
      return `<tr>
        <td><span class="mp-cls sol-fee ${t.cls || ""}">${t.cls || "?"}</span></td>
        <td class="mono" title="${slot}">${slot}</td>
        <td><b>${fmtUl(t.fee)}</b></td>
        <td class="dim">${t.vs_med != null ? fmt.num(t.vs_med, 1) + "×" : "—"}</td>
        <td>${link}</td>
      </tr>`;
    }).join("");
  };

  const updateSolMempool = (sol, hist) => {
    const m = sol.mempool || {};
    const meta = m.meta || {};
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    set("sol-mp-count", fmt.num(m.count, 0));
    set("sol-mp-queued", fmt.num(meta.slots != null ? meta.slots : m.queued, 0), "dim");
    set("sol-mp-mev-live-n", meta.median_fee != null ? fmtUl(meta.median_fee) : "--", "amber");
    set("sol-mp-mev-share", meta.p90_fee != null ? fmtUl(meta.p90_fee) : "--", "dim");
    const badge = $("sol-mp-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "mp-pressure-badge " + (meta.pressure || "idle");
    }
    const metaEl = $("sol-mp-meta");
    if (metaEl) {
      const tps = meta.tps != null ? fmt.num(meta.tps, 0) : "--";
      const nv = meta.nv_tps != null ? fmt.num(meta.nv_tps, 0) : "--";
      metaEl.innerHTML =
        `<span>p99 <b>${fmtUl(meta.p99_fee)}</b></span>` +
        `<span>max <b>${fmtUl(meta.max_fee)}</b></span>` +
        `<span>avg <b>${fmtUl(meta.avg_fee)}</b></span>` +
        `<span>zero <b>${fmt.num(meta.zero_pct, 0)}%</b></span>` +
        `<span>hot <b>${fmt.num(meta.hot_share_pct, 0)}%</b></span>` +
        `<span>TPS <b>${tps}</b></span>` +
        `<span>non-vote <b>${nv}</b></span>` +
        `<span class="dim">µl / CU</span>`;
    }
    const track = $("sol-mp-mix-track");
    const keys = $("sol-mp-mev");
    const mev = m.mev || {};
    const total = Object.values(mev).reduce((a, b) => a + (Number(b) || 0), 0) || 1;
    const mixKeys = SOL_FEE_ORDER.filter((k) => mev[k] != null).concat(
      Object.keys(mev).filter((k) => !SOL_FEE_ORDER.includes(k))
    );
    if (track) {
      track.innerHTML = mixKeys.map((k) =>
        `<span class="sol-fee-seg ${k}" style="width:${Math.max(2, 100 * (Number(mev[k]) || 0) / total)}%;background:${SOL_FEE_COLORS[k] || "#64748b"}" title="${k} ${mev[k]}"></span>`
      ).join("");
    }
    if (keys) {
      keys.innerHTML = mixKeys.map((k) =>
        `<span><i style="display:inline-block;width:7px;height:7px;border-radius:2px;background:${SOL_FEE_COLORS[k] || "#64748b"};margin-right:4px"></i>${k} <b>${mev[k]}</b></span>`
      ).join("") || `<span class="dim">scanning priority fees…</span>`;
    }
    solMpLiveCache = m.mev_txs || [];
    renderSolMpLive();
    const topBody = $("sol-mp-top-table") && $("sol-mp-top-table").querySelector("tbody");
    if (topBody) {
      const rows = m.top_to || [];
      topBody.innerHTML = rows.length
        ? rows.map((t) =>
            `<tr>
              <td>${t.label || ""}</td>
              <td><div class="mp-bar-track"><div class="mp-bar mev" style="width:${Math.min(100, t.pct || 0)}%"></div></div></td>
              <td>${fmt.num(t.txs, 0)}</td>
              <td class="dim">${fmt.num(t.pct, 0)}%</td>
            </tr>`
          ).join("")
        : `<tr><td colspan="4" class="dim">waiting for histogram…</td></tr>`;
    }
    const spokeEmpty = $("sol-mp-spoke-empty");
    const spokeBody = $("sol-mp-spoke") && $("sol-mp-spoke").querySelector("tbody");
    const spokes = m.spoke_txs || [];
    if (spokeEmpty) spokeEmpty.style.display = spokes.length ? "none" : "block";
    if (spokeBody) {
      spokeBody.innerHTML = spokes.map((t) => {
        const slot = t.user != null ? String(t.user) : "--";
        const link = t.solscan
          ? `<a href="${t.solscan}" target="_blank" rel="noopener" style="color:var(--violet)">solscan</a>`
          : "";
        return `<tr>
          <td><span class="mp-cls sol-fee ${t.cls || t.fn || ""}">${t.cls || t.fn || ""}</span></td>
          <td class="mono">${slot}</td>
          <td><b>${fmtUl(t.args)}</b></td>
          <td>${link}</td>
        </tr>`;
      }).join("");
    }
    const hotNote = $("sol-mp-spoke-note");
    if (hotNote) hotNote.textContent = String(spokes.length);
    const contested = $("sol-mp-contested");
    if (contested) {
      contested.innerHTML = meta.tps != null
        ? `<span>cluster TPS <b>${fmt.num(meta.tps, 0)}</b></span>` +
          (meta.nv_tps != null ? `<span>non-vote <b>${fmt.num(meta.nv_tps, 0)}</b></span>` : "") +
          `<span>hot share <b>${fmt.num(meta.hot_share_pct, 0)}%</b></span>`
        : "";
    }
    const feeMed = (hist && hist.sol_fee_median) || [];
    const feeP90 = (hist && hist.sol_fee_p90) || [];
    const tpsH = (hist && hist.sol_tps) || [];
    if (solChartTx && (feeMed.length || feeP90.length || tpsH.length)) {
      const n = Math.max(feeMed.length, feeP90.length, tpsH.length);
      solChartTx.data.labels = Array.from({ length: n }, (_, i) => i);
      solChartTx.data.datasets[0].data = feeMed;
      solChartTx.data.datasets[1].data = feeP90;
      solChartTx.data.datasets[2].data = tpsH;
      solChartTx.update("none");
    }
  };

  const renderSolOpps = () => {
    const body = $("sol-opps-table") && $("sol-opps-table").querySelector("tbody");
    const empty = $("sol-opps-empty");
    if (!body) return;
    let rows = solOpCache;
    if (solOpFilter === "edge") rows = rows.filter((o) => o.edge);
    else if (solOpFilter === "profit") rows = rows.filter((o) => Number(o.profit_usd) > 0);
    else if (solOpFilter === "hot") rows = rows.filter((o) => o.urgency === "hot");
    else if (solOpFilter === "elev") rows = rows.filter((o) => o.urgency === "elevated" || o.urgency === "hot");
    if (empty) empty.style.display = rows.length ? "none" : "block";
    body.innerHTML = rows.slice(0, 40).map((o) =>
      `<tr><td class="mono">${o.user || o.symbol || ""}</td>
        <td>${o.hf != null ? fmt.num(o.hf, 3) : "--"}</td>
        <td>${o.collateral_sym || "?"} → ${o.debt_sym || "?"}</td>
        <td>${o.profit_usd != null ? fmt.usd(o.profit_usd) : "—"}</td>
        <td>${o.util_pct != null ? fmt.num(o.util_pct, 1) + "%" : (o.edge ? "edge" : "—")}</td>
        <td>${o.urgency || ""}</td></tr>`
    ).join("");
  };

  const updateSolOpps = (sol) => {
    const meta = sol.opportunities_meta || {};
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    set("sol-op-count", fmt.num(meta.count, 0));
    set("sol-op-best", meta.best_profit ? fmt.usd(meta.best_profit) : "--", "green");
    set("sol-op-edge-n", fmt.num(meta.edge_n, 0), "amber");
    set("sol-op-sweep", `${fmt.num(meta.sweep_total, 0)} / ${fmt.num(meta.watch_n, 0)}`, "dim");
    const badge = $("sol-op-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "op-pressure-badge " + (meta.pressure || "idle");
    }
    const metaEl = $("sol-op-meta");
    if (metaEl) {
      metaEl.innerHTML =
        `<span>status <b>${meta.status || "—"}</b></span>` +
        `<span>scan <b>${meta.scan_ms != null ? meta.scan_ms + "ms" : "--"}</b></span>` +
        `<span>gpa <b>${meta.obligation_probed != null ? meta.obligation_probed : "—"}</b></span>` +
        `<span>${meta.note || "watchlist = reserve util"}</span>`;
    }
    const wl = sol.watchlist || [];
    const closest = wl[0];
    set("sol-op-closest-hf", closest && closest.hf != null ? fmt.num(closest.hf, 3) : "--");
    const cu = $("sol-op-closest-user");
    if (cu) cu.textContent = closest ? (closest.symbol || closest.user || "") : "--";
    const urg = $("sol-op-urgency");
    if (urg) urg.textContent = closest ? (closest.urgency || "") + (closest.util_pct != null ? ` · ${closest.util_pct}% util` : "") : "";
    const mix = meta.pair_mix || [];
    const track = $("sol-op-mix-track");
    const keys = $("sol-op-mix-keys");
    const tot = mix.reduce((a, m) => a + (m.n || 0), 0) || 1;
    if (track) {
      track.innerHTML = mix.map((m, i) =>
        `<i style="width:${Math.max(4, 100 * (m.n || 0) / tot)}%;background:${palette[i % palette.length]}"></i>`
      ).join("");
    }
    if (keys) {
      keys.innerHTML = mix.map((m) => `<span>${m.pair} <b>${m.n}</b></span>`).join("")
        || `<span class="dim">scanning Solend…</span>`;
    }
    // opportunities empty; watchlist shown in both feed (as util rows) + watch table
    solOpCache = (sol.opportunities || []).length
      ? sol.opportunities
      : wl.map((w) => ({ ...w, profit_usd: null, edge: false }));
    renderSolOpps();
    const wbody = $("sol-watch-table") && $("sol-watch-table").querySelector("tbody");
    const wnote = $("sol-watch-note");
    if (wnote) wnote.textContent = String(wl.length);
    if (wbody) {
      wbody.innerHTML = wl.slice(0, 30).map((w) =>
        `<tr><td>${w.symbol || w.user || ""}</td>
          <td>${w.hf != null ? fmt.num(w.hf, 3) : "--"}</td>
          <td>${w.supply_apy != null ? w.supply_apy + "%" : (w.collateral_sym || "")}</td>
          <td>${w.borrow_apy != null ? w.borrow_apy + "%" : (w.debt_sym || "")}</td>
          <td>${w.urgency || (w.util_pct != null ? w.util_pct + "%" : "")}</td></tr>`
      ).join("");
    }
  };

  const renderSolComps = () => {
    const body = $("sol-comp-table") && $("sol-comp-table").querySelector("tbody");
    const empty = $("sol-comp-empty");
    if (!body) return;
    let rows = solCpCache;
    if (solCpFilter === "miss") rows = rows.filter((r) => r.missed);
    else if (solCpFilter === "edge") rows = rows.filter((r) => r.edge);
    else if (solCpFilter === "profit") rows = rows.filter((r) => Number(r.est) > 0);
    else if (solCpFilter === "revert") rows = rows.filter((r) => /revert/i.test(r.flags || ""));
    if (empty) empty.style.display = rows.length ? "none" : "block";
    body.innerHTML = rows.slice(0, 40).map((r) =>
      `<tr><td>${r.slot || r.age || ""}</td><td>${r.pair || ""}</td><td>${r.searcher || ""}</td>
        <td>${r.user || ""}</td><td>${r.gas_usd != null ? fmt.usd(r.gas_usd) : "—"}</td>
        <td>${r.est != null ? fmt.usd(r.est) : "—"}</td>
        <td>${r.net != null ? fmt.usd(r.net) : "—"}</td>
        <td>${r.flags || ""}</td><td class="mono">${r.tx || ""}</td></tr>`
    ).join("");
  };

  const updateSolCompetitors = (sol, hist) => {
    const meta = sol.competitors_meta || {};
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    set("sol-cp-count", fmt.num(meta.count_1h, 0));
    set("sol-cp-searchers", fmt.num(meta.unique_searchers, 0), "dim");
    set("sol-cp-sum-est", meta.sum_est_profit ? fmt.usd(meta.sum_est_profit) : "—", "amber");
    set("sol-cp-missed", fmt.num(meta.missed_by_us, 0), "red");
    const badge = $("sol-cp-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "cp-pressure-badge " + (meta.pressure || "idle");
    }
    const metaEl = $("sol-cp-meta");
    if (metaEl) {
      metaEl.innerHTML =
        `<span>status <b>${meta.status || "—"}</b></span>` +
        `<span>reverts <b>${fmt.num(meta.revert_n, 0)}</b></span>` +
        `<span>${meta.note || ""}</span>`;
    }
    solCpCache = sol.competitors || [];
    renderSolComps();
    const sbody = $("sol-cp-searcher-table") && $("sol-cp-searcher-table").querySelector("tbody");
    const sempty = $("sol-cp-searcher-empty");
    const tops = meta.top_searchers || [];
    if (sempty) sempty.style.display = tops.length ? "none" : "block";
    if (sbody) {
      sbody.innerHTML = tops.map((t, i) =>
        `<tr><td>${i + 1}</td><td>${t.searcher}</td><td>${t.share ?? ""}</td><td>${t.n}</td><td>${t.sum_est ?? "—"}</td></tr>`
      ).join("");
    }
    const pbody = $("sol-cp-pair-table") && $("sol-cp-pair-table").querySelector("tbody");
    const pnote = $("sol-cp-pair-note");
    const mix = meta.pair_mix || [];
    if (pnote) pnote.textContent = String(mix.length);
    if (pbody) {
      pbody.innerHTML = mix.map((p) =>
        `<tr><td>${p.pair}</td><td>${p.share ?? ""}</td><td>${p.n}</td><td>${p.pct ?? ""}%</td></tr>`
      ).join("");
    }
    const track = $("sol-cp-mix-track");
    const keys = $("sol-cp-mix-keys");
    const tot = mix.reduce((a, m) => a + (m.n || 0), 0) || 1;
    if (track) track.innerHTML = mix.map((m, i) =>
      `<i style="width:${Math.max(4, 100 * (m.n || 0) / tot)}%;background:${palette[i % palette.length]}"></i>`
    ).join("");
    if (keys) keys.innerHTML = mix.map((m) => `<span>${m.pair} <b>${m.n}</b></span>`).join("")
      || `<span class="dim">scanning…</span>`;
    const ch = (hist && hist.sol_comp_1h) || [];
    if (solChartComp && ch.length) {
      solChartComp.data.labels = ch.map((_, i) => i);
      solChartComp.data.datasets[0].data = ch;
      solChartComp.data.datasets[1].data = ch.map(() => 0);
      solChartComp.update("none");
    }
  };

  const renderSolArb = () => {
    const body = $("sol-arb-table") && $("sol-arb-table").querySelector("tbody");
    const empty = $("sol-arb-empty");
    if (!body) return;
    let rows = solArCache;
    if (solArFilter === "live") rows = rows.filter((o) => Number(o.net_usd) > 0);
    else if (solArFilter === "cross") rows = rows.filter((o) => o.cross_dex);
    else if (solArFilter === "jup") rows = rows.filter((o) => /jup|jupiter/i.test(o.dex || o.venue || ""));
    else if (solArFilter === "near") rows = rows.filter((o) => o.gap_usd != null);
    if (empty) empty.style.display = rows.length ? "none" : "block";
    body.innerHTML = rows.slice(0, 40).map((o) =>
      `<tr><td>${o.venue || ""}</td><td>${o.path || ""}</td><td>${o.route || ""}</td>
        <td>${o.hops ?? ""}</td><td>${o.borrow || ""}</td>
        <td>${fmt.usd(o.gross_usd, 4)}</td><td>${fmt.usd(o.gas_usd, 4)}</td>
        <td class="${(o.net_usd || 0) > 0 ? "green" : "dim"}">${fmt.usd(o.net_usd, 4)}</td>
        <td>${o.flags || ""}</td></tr>`
    ).join("");
  };

  const updateSolArb = (sol, hist) => {
    const a = sol.arb || {};
    const meta = a.meta || {};
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    set("sol-ar-live", fmt.num(meta.live, 0));
    set("sol-ar-actionable", fmt.num(meta.actionable, 0), "green");
    set("sol-ar-best-net", meta.best_net_usd != null ? fmt.usd(meta.best_net_usd, 4) : "--", "amber");
    set("sol-ar-near-n", fmt.num(meta.near, 0), "dim");
    const badge = $("sol-ar-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "ar-pressure-badge " + (meta.pressure || "idle");
    }
    const metaEl = $("sol-arb-meta");
    if (metaEl) {
      metaEl.innerHTML =
        `<span>mode <b>${meta.mode || "jup"}</b></span>` +
        `<span>scan <b>${meta.scan_ms != null ? meta.scan_ms + "ms" : "--"}</b></span>` +
        `<span>slot <b>${meta.scan_slot != null ? fmt.num(meta.scan_slot, 0) : "--"}</b></span>` +
        `<span>prio tip <b>${meta.tip_usd != null ? fmt.usd(meta.tip_usd, 4) : "--"}</b></span>` +
        `<span>quotes <b>${meta.quotes != null ? meta.quotes : "--"}</b></span>` +
        (a.error ? `<span class="red">${a.error}</span>` : "");
    }
    const mix = meta.venue_mix || [];
    const track = $("sol-ar-mix-track");
    const keys = $("sol-ar-mix-keys");
    const tot = mix.reduce((x, m) => x + (m.n || 0), 0) || 1;
    if (track) track.innerHTML = mix.map((m, i) =>
      `<i style="width:${Math.max(4, 100 * (m.n || 0) / tot)}%;background:${palette[i % palette.length]}"></i>`
    ).join("");
    if (keys) keys.innerHTML = mix.map((m) => `<span>${m.venue} <b>${m.n}</b></span>`).join("")
      || `<span class="dim">scanning Jupiter…</span>`;
    solArCache = [].concat(a.opps || [], (a.near || []).map((n) => ({ ...n, flags: (n.flags || "") + " near" })));
    renderSolArb();
    const nbody = $("sol-arb-near-table") && $("sol-arb-near-table").querySelector("tbody");
    const nempty = $("sol-arb-near-empty");
    const near = a.near || [];
    if (nempty) nempty.style.display = near.length ? "none" : "block";
    if (nbody) {
      nbody.innerHTML = near.slice(0, 20).map((o) =>
        `<tr><td>${o.venue || ""}</td><td>${o.path || ""}</td>
          <td>${fmt.usd(o.gross_usd, 4)}</td><td>${fmt.usd(o.gap_usd, 4)}</td>
          <td>${o.roi != null ? fmt.num(o.roi, 4) : "--"}</td></tr>`
      ).join("");
    }
    const cov = $("sol-arb-stats");
    if (cov) {
      const st = a.stats || {};
      cov.innerHTML =
        `<span>quotes <b>${st.quotes ?? 0}</b></span>` +
        `<span>pairs <b>${st.pairs_tried ?? "—"}</b></span>` +
        `<span>mids <b>${(st.mids || []).join(", ") || "—"}</b></span>` +
        `<span>tip <b>${st.tip_usd != null ? fmt.usd(st.tip_usd, 4) : "—"}</b></span>` +
        `<span>venues <b>${(st.venues || []).slice(0, 4).join(", ") || "—"}</b></span>` +
        `<span>dex <b>${(st.dexes || []).slice(0, 6).join(", ") || "—"}</b></span>`;
    }
    const bn = (hist && hist.sol_arb_best_net) || [];
    const ac = (hist && hist.sol_arb_actionable) || [];
    if (solChartArb && (bn.length || ac.length)) {
      const n = Math.max(bn.length, ac.length);
      solChartArb.data.labels = Array.from({ length: n }, (_, i) => i);
      solChartArb.data.datasets[0].data = bn;
      solChartArb.data.datasets[1].data = ac;
      solChartArb.update("none");
    }
  };

  const updateSolBroadcast = (sol) => {
    const bc = sol.broadcast || {};
    const ready = bc.ready || {};
    const sum = bc.summary || {};
    const setTxt = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    let pressure = sum.pressure || bc.pressure || "idle";
    let label = sum.label || pressure;
    const badge = $("sol-bc-pressure");
    if (badge) {
      badge.textContent = label;
      badge.className = "bc-pressure-badge " + pressure;
    }
    setTxt("sol-bc-liq", ready.liq ? "ready" : "blocked", ready.liq ? "green" : "red");
    setTxt("sol-bc-arb", ready.arb ? "ready" : "blocked", ready.arb ? "green" : "red");
    setTxt("sol-bc-dyn-liq", bc.dyn_min_liq != null ? "$" + fmt.num(bc.dyn_min_liq) : "--", "amber");
    setTxt("sol-bc-dyn-arb", bc.dyn_min_arb != null ? "$" + fmt.num(bc.dyn_min_arb) : "--", "amber");
    setTxt("sol-bc-last-stage", sum.last_stage || "--", "dim");
    const st = $("sol-bcast-status");
    if (st) {
      st.innerHTML =
        `<span>sim <b>${bc.sim_only ? "ON" : "off"}</b></span>` +
        `<span>armed <b>${bc.armed ? "LIVE" : "no"}</b></span>` +
        (bc.liq_contract ? `<span>liq <b>${String(bc.liq_contract).slice(0, 10)}…</b></span>` : `<span>liq <b>unset</b></span>`) +
        (bc.arb_contract ? `<span>arb <b>${String(bc.arb_contract).slice(0, 10)}…</b></span>` : `<span>arb <b>unset</b></span>`);
    }
    const pills = $("sol-bc-mode-pills");
    if (pills) {
      pills.innerHTML =
        `<span class="bc-pill ${bc.sim_only ? "on" : ""}">sim ${bc.sim_only ? "ON" : "off"}</span>` +
        `<span class="bc-pill ${bc.armed ? "live" : ""}">armed ${bc.armed ? "LIVE" : "no"}</span>` +
        `<span class="bc-pill ${bc.edge_bias ? "on" : ""}">edge ${bc.edge_bias ? "on" : "off"}</span>` +
        `<span class="bc-pill warn">gates blocked</span>`;
    }
    const btnSim = $("sol-btn-sim");
    const btnArm = $("sol-btn-arm");
    const btnEdge = $("sol-btn-edge");
    if (btnSim) {
      btnSim.classList.toggle("on-sim", !!bc.sim_only);
      btnSim.textContent = bc.sim_only ? "Sim ON" : "Sim-only";
    }
    if (btnArm) {
      btnArm.classList.toggle("on-arm", !!bc.armed);
      btnArm.textContent = bc.armed ? "Disarm LIVE" : "Arm LIVE 15m";
    }
    if (btnEdge) {
      btnEdge.classList.toggle("on-edge", !!bc.edge_bias);
      btnEdge.textContent = bc.edge_bias ? "Edge ON" : "Edge bias";
    }
    const summary = $("sol-bc-summary");
    if (summary) {
      summary.innerHTML =
        `<span>hist <b>${fmt.num(sum.n_hist, 0)}</b></span>` +
        `<span>sent <b style="color:var(--green)">${fmt.num(sum.n_sent, 0)}</b></span>` +
        `<span>sim <b style="color:var(--cyan)">${fmt.num(sum.n_sim, 0)}</b></span>` +
        `<span>skips <b style="color:var(--amber)">${fmt.num(sum.n_skip, 0)}</b></span>`;
    }
    const rs = $("sol-bcast-reasons");
    if (rs) {
      rs.innerHTML = (ready.reasons || []).length
        ? ready.reasons.map((r) => `<span style="color:var(--amber)">${r}</span>`).join("")
        : "<span style=\"color:var(--green)\">ready</span>";
    }
    const near = $("sol-bcast-near");
    if (near) {
      near.innerHTML = (bc.near_miss_hints || []).length
        ? bc.near_miss_hints.map((h) => `<span>${JSON.stringify(h)}</span>`).join("")
        : "<span class=\"dim\">no near-miss data yet</span>";
    }
    const hist = bc.history || [];
    const skipped = bc.skipped || [];
    solBcRowsCache = [].concat(hist, skipped.map((x) => ({ ...x, kind: x.kind || "skip", stage: x.stage || "skip" })));
    solBcRowsCache.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    const body = $("sol-bcast-table") && $("sol-bcast-table").querySelector("tbody");
    const hempty = $("sol-bc-hist-empty");
    let rows = solBcRowsCache;
    if (solBcFilter === "liq") rows = rows.filter((r) => r.kind === "liq");
    else if (solBcFilter === "arb") rows = rows.filter((r) => r.kind === "arb");
    else if (solBcFilter === "skip") rows = rows.filter((r) => (r.kind === "skip") || /skip/i.test(r.stage || ""));
    else if (solBcFilter === "sent") rows = rows.filter((r) => /sent|ok/i.test(r.stage || ""));
    else if (solBcFilter === "sim") rows = rows.filter((r) => /sim/i.test(r.stage || ""));
    if (hempty) hempty.style.display = rows.length ? "none" : "block";
    if (body) {
      body.innerHTML = rows.slice(0, 40).map((h) =>
        `<tr><td>${fmt.ts(h.ts)}</td><td>${h.kind || ""}</td><td>${h.stage || ""}</td>
          <td class="args">${h.detail || h.why || ""}</td></tr>`
      ).join("");
    }
    const rbody = $("sol-bc-recent-table") && $("sol-bc-recent-table").querySelector("tbody");
    const rempty = $("sol-bc-recent-empty");
    const recent = hist.slice(0, 8);
    if (rempty) rempty.style.display = recent.length ? "none" : "block";
    if (rbody) {
      rbody.innerHTML = recent.map((h) =>
        `<tr><td>${h.kind || ""}</td><td>${h.stage || ""}</td><td class="args">${h.detail || ""}</td></tr>`
      ).join("");
    }
  };

  const updateSolIntel = (sol) => {
    const intel = sol.intel || {};
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    set("sol-intel-act", intel.act_p != null ? fmt.num(intel.act_p, 2) : "--");
    set("sol-intel-exp", intel.exp_net != null ? fmt.usd(intel.exp_net, 4) : "--", "amber");
    set("sol-intel-steps", fmt.num(intel.steps, 0), "dim");
    set("sol-intel-records", fmt.num(intel.records, 0));
    const advice = $("sol-in-advice");
    if (advice) advice.textContent = (intel.brain && intel.brain.advice) || "warming up";
    const badge = $("sol-in-pressure");
    if (badge) {
      const p = intel.readiness >= 70 ? "elevated" : intel.readiness >= 30 ? "quiet" : "idle";
      badge.textContent = p;
      badge.className = "in-pressure-badge " + p;
    }
    const pct = $("sol-in-ready-pct");
    if (pct) pct.textContent = String(intel.readiness != null ? intel.readiness : 0);
    if (solGauge) {
      const r = Math.max(0, Math.min(100, Number(intel.readiness) || 0));
      solGauge.data.datasets[0].data = [r, 100 - r];
      solGauge.update("none");
    }
    const hours = intel.hours || {};
    if (solChartHours) {
      solChartHours.data.datasets[0].data = Array.from({ length: 24 }, (_, h) => hours[String(h)] || 0);
      solChartHours.update("none");
    }
    const dows = intel.dows || {};
    if (solChartDows) {
      solChartDows.data.datasets[0].data = Array.from({ length: 7 }, (_, d) => dows[String(d)] || 0);
      solChartDows.update("none");
    }
    const brain = $("sol-intel-brain");
    if (brain) {
      const b = intel.brain || {};
      brain.innerHTML =
        `<span>protocol <b>${b.protocol || sol.protocol || "Solend"}</b></span>` +
        `<span>liq× <b>${fmt.num(b.min_liq_mult, 2)}</b></span>` +
        `<span>arb× <b>${fmt.num(b.min_arb_mult, 2)}</b></span>` +
        `<span>edge <b>${b.prefer_edge ? "on" : "off"}</b></span>`;
    }
    const mev = intel.mev || {};
    const track = $("sol-in-mix-track");
    const keys = $("sol-intel-mev");
    const tot = Object.values(mev).reduce((a, b) => a + (Number(b) || 0), 0) || 1;
    if (track) track.innerHTML = Object.entries(mev).map(([k, v]) =>
      `<i style="width:${Math.max(4, 100 * (Number(v) || 0) / tot)}%;background:#a78bfa"></i>`
    ).join("");
    if (keys) keys.innerHTML = Object.entries(mev).map(([k, v]) =>
      `<span>${k} <b>${v}</b></span>`
    ).join("") || `<span class="dim">waiting…</span>`;
    if (intel.act_p != null) {
      solIntelTrendHist.push(intel.act_p);
      if (solIntelTrendHist.length > 60) solIntelTrendHist.shift();
      if (solChartIntelTrend) {
        solChartIntelTrend.data.labels = solIntelTrendHist.map((_, i) => i);
        solChartIntelTrend.data.datasets[0].data = solIntelTrendHist;
        solChartIntelTrend.update("none");
      }
    }
  };

  const updateSolPrices = (sol) => {
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    if (sol.sol_price_usd != null) set("sol-mc-price", fmt.usd(sol.sol_price_usd));
    set("sol-mc-gas", sol.priority_fee != null ? fmt.num(sol.priority_fee, 0) + " µl" : "--", "amber");
    set("sol-mc-updated", sastClock(), "dim");
    const meta = $("sol-mc-meta");
    if (meta) {
      meta.innerHTML =
        `<span>slot <b>${sol.slot != null ? fmt.num(sol.slot, 0) : "--"}</b></span>` +
        `<span>epoch <b>${sol.epoch != null ? fmt.num(sol.epoch, 0) : "--"}</b></span>` +
        `<span>rpc <b>${(sol.rpc || "").replace(/^https?:\/\//, "").slice(0, 28) || "--"}</b></span>`;
    }
    const reserves = $("sol-reserves-list");
    const res = (sol.prices && sol.prices.reserves) || {};
    if (reserves) {
      const keys = Object.keys(res);
      reserves.innerHTML = keys.length
        ? keys.map((k) => {
            const r = res[k] || {};
            return `<span>${k} <b>util ${r.util_pct ?? "--"}%</b> borrow ${r.borrow_apy ?? "--"}%</span>`;
          }).join("")
        : `<span class="dim">Solend reserves loading…</span>`;
    }
    const delta = $("sol-res-delta");
    if (delta) delta.textContent = sol.protocol || "Solend";
  };

  const updateSolLog = (s) => {
    const lines = (s.log || []).filter((l) => String(l.cat || "").startsWith("sol"));
    solLogLines = lines.slice(-200);
    const feed = $("sol-log");
    if (!feed) return;
    const q = (solAlSearch || "").toLowerCase();
    const shown = solLogLines.filter((l) => {
      if (solAlFilter !== "all" && l.level !== solAlFilter) return false;
      if (solAlCat !== "all" && l.cat !== solAlCat) return false;
      if (q && !(String(l.msg || "").toLowerCase().includes(q) || String(l.cat || "").toLowerCase().includes(q)))
        return false;
      return true;
    });
    feed.innerHTML = shown.slice(-120).map((l) =>
      `<div class="ln ${l.level || "info"}"><span class="t">${fmt.ts(l.ts)}</span>
        <span class="c">${l.cat || ""}</span><span class="m"></span></div>`
    ).join("");
    feed.querySelectorAll(".ln").forEach((row, i) => {
      const m = row.querySelector(".m");
      if (m) m.textContent = shown.slice(-120)[i].msg || "";
    });
    const meta = s.sol && s.sol.log_meta;
    const byLvl = {};
    solLogLines.forEach((l) => { byLvl[l.level || "info"] = (byLvl[l.level || "info"] || 0) + 1; });
    const set = (id, v) => { const e = $(id); if (e) e.textContent = v; };
    set("sol-al-lines", String(solLogLines.length));
    set("sol-al-n-info", String(byLvl.info || 0));
    set("sol-al-n-warn", String(byLvl.warn || 0));
    set("sol-al-n-error", String(byLvl.error || 0));
    set("sol-al-n-ok", String((byLvl.ok || 0) + (byLvl.money || 0)));
    set("sol-al-errors", String(byLvl.error || 0));
    set("sol-al-shown", shown.length + " shown");
    set("sol-al-last", solLogLines.length ? fmt.ts(solLogLines[solLogLines.length - 1].ts) : "--");
    if (solAlAutoscroll && feed) feed.scrollTop = feed.scrollHeight;
  };

  const renderSol = (s) => {
    const sol = s.sol || {};
    window.__lastSolBcast = sol.broadcast || {};
    updateSolHeader(s);
    updateSolBots(sol);
    updateSolFunds(sol);
    updateSolMempool(sol, s.hist);
    updateSolOpps(sol);
    updateSolCompetitors(sol, s.hist);
    updateSolArb(sol, s.hist);
    updateSolBroadcast(sol);
    updateSolIntel(sol);
    updateSolPrices(sol);
    updateSolLog(s);
  };

  const postSolControl = async (body) => {
    try {
      const r = await fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, chain: "sol" }),
      });
      return await r.json();
    } catch (e) {
      return null;
    }
  };

  const bindSolFilters = () => {
    const bind = (id, cls, setter, redraw) => {
      const root = $(id);
      if (!root || root.__bound) return;
      root.__bound = true;
      root.addEventListener("click", (ev) => {
        const btn = ev.target.closest("." + cls);
        if (!btn) return;
        setter(btn.dataset.f || "all");
        root.querySelectorAll("." + cls).forEach((b) => b.classList.toggle("on", b === btn));
        redraw();
      });
    };
    bind("sol-mp-filters", "mp-f", (v) => { solMpFilter = v; }, renderSolMpLive);
    bind("sol-op-filters", "op-f", (v) => { solOpFilter = v; }, renderSolOpps);
    bind("sol-cp-filters", "cp-f", (v) => { solCpFilter = v; }, renderSolComps);
    bind("sol-ar-filters", "ar-f", (v) => { solArFilter = v; }, renderSolArb);
    bind("sol-bc-filters", "bc-f", (v) => { solBcFilter = v; }, () => {
      if (window.__lastState) updateSolBroadcast(window.__lastState.sol || {});
    });
    const fund = $("sol-btn-fund");
    if (fund && !fund.__bound) {
      fund.__bound = true;
      fund.addEventListener("click", () => {
        const st = $("sol-fund-status");
        const s = (window.__lastState && window.__lastState.sol) || {};
        const funds = s.funds || {};
        const w = s.wallets || {};
        const g = s.fund_guide || {};
        const fd = (funds.funder || {}).pubkey || g.from_pubkey || w.funder || "";
        const sp = (funds.sponsor || {}).pubkey || g.sponsor || w.sponsor || "";
        const bt = (funds.bot || {}).pubkey || g.bot || w.bot || "";
        const ts = g.sponsor_target_sol != null ? g.sponsor_target_sol : 0.08;
        const tb = g.bot_target_sol != null ? g.bot_target_sol : 0.25;
        const text =
          `From funder ${fd}\n${ts} SOL → sponsor ${sp}\n${tb} SOL → bot ${bt}`;
        if (!sp || !bt) {
          if (st) st.textContent = "sponsor/bot pubkeys missing — restart dashboard";
          return;
        }
        navigator.clipboard.writeText(text).then(() => {
          if (st) st.textContent = "copied: funder → sponsor + bot amounts and addresses";
        }).catch(() => {
          if (st) st.textContent = text.replace(/\n/g, " · ");
        });
      });
    }
  };


  /* ------------------------------------------------ dom renderers */
  const updateHeader = (s) => {
    if (typeof activeTab !== "undefined" && activeTab === "sol") {
      updateSolHeader(s);
      $("sys-info").textContent = "uptime " + fmt.age(s.started) + " | ws " + (s.now ? "live" : "--");
      return;
    }
    const lblB = $("p-block-lbl"), lblG = $("p-gas-lbl"), lblE = $("p-eth-lbl");
    if (lblB) lblB.textContent = "block";
    if (lblG) lblG.textContent = "gas";
    if (lblE) lblE.textContent = "ETH";
    $("p-block").textContent = s.block || "--";
    $("p-gas").textContent = (s.gas_gwei != null ? s.gas_gwei + " gwei" : "--");
    $("p-gas").style.color = s.gas_class === "hot" ? "var(--red)" : s.gas_class === "normal" ? "var(--amber)" : "var(--green)";
    $("p-eth").textContent = (s.eth_price_usd != null ? fmt.usd(s.eth_price_usd) : "--");
    $("p-ready").textContent = (s.intel ? s.intel.readiness : 0) + "%";
    const bc = s.broadcast || {};
    const ready = bc.ready || {};
    const bcastEl = $("p-bcast");
    if (bcastEl) {
      if (!bc.enabled) bcastEl.textContent = "off";
      else if (ready.liq || ready.arb) bcastEl.textContent =
        (ready.liq ? "liq" : "") + (ready.liq && ready.arb ? "+" : "") + (ready.arb ? "arb" : "");
      else bcastEl.textContent = "blocked";
      bcastEl.style.color = (!bc.enabled ? "var(--amber)"
        : (ready.liq || ready.arb) ? "var(--green)" : "var(--red)");
    }
    $("sys-info").textContent = "uptime " + fmt.age(s.started) + " | ws " + (s.now ? "live" : "--");
  };

  const updateBots = (s) => {
    const el = $("bots-list");
    const labels = { mempool: "Mempool Watcher", prices: "Oracle / Prices", funds: "Funds Balances",
                     sweep: "HF Opportunity Sweep", competitors: "Competitor Watch", arb: "DEX Arb Scanner",
                     intel: "Learning / Intel", broadcast: "Broadcast Submit" };
    el.innerHTML = Object.entries(labels).map(([k, name]) => {
      const b = s.bots[k] || {};
      const age = b.last ? fmt.age(b.last) + " ago" : "never";
      return `<div class="bot">
        <span class="st ${b.status || "idle"}"></span>
        <div><div class="b-name">${name}</div>
          <div class="b-last">${age}</div>
          <div class="b-msg">${b.msg || b.status || ""}</div></div></div>`;
    }).join("");
  };

  const usd = (v, signed) => {
    if (v == null || Number.isNaN(+v)) return "--";
    const n = +v;
    const sign = signed ? (n > 0 ? "+" : n < 0 ? "−" : "") : "";
    return sign + "$" + fmt.num(Math.abs(n), 2);
  };

  const updateFunds = (s) => {
    const p = s.performance || {};
    const gradeEl = $("fp-grade");
    if (gradeEl) {
      const g = p.grade || "—";
      gradeEl.textContent = g;
      gradeEl.className = "fp-grade g-" + String(g).replace("—", "na");
    }
    const verd = $("fp-verdict");
    if (verd) verd.textContent = p.verdict || "waiting for funds snapshot…";
    const set = (id, txt, cls) => {
      const el = $(id);
      if (!el) return;
      el.textContent = txt;
      if (cls != null) el.className = "fp-v " + cls;
    };
    set("fp-equity", usd(p.equity_usd));
    const pnl = p.session_pnl_usd;
    set("fp-pnl", usd(pnl, true),
        pnl == null ? "" : (pnl > 0 ? "green" : pnl < 0 ? "red" : ""));
    set("fp-realized", usd(p.realized_usd), "green");
    set("fp-sim", usd(p.simulated_usd), "amber");
    set("fp-hit",
        (p.submits ? `${fmt.num(p.hit_rate_pct, 0)}% (${p.wins}/${p.submits})` : "—"));
    set("fp-missed",
        p.missed_comp_n
          ? `${usd(p.missed_comp_usd)} · ${p.missed_comp_n}`
          : usd(p.missed_comp_usd || 0),
        "red");
    const edge = $("fp-edge");
    if (edge) {
      const bits = [];
      if (p.best_opp_usd > 0) bits.push(`best liq $${fmt.num(p.best_opp_usd, 2)}`);
      if (p.arb_best_net_usd != null)
        bits.push(`arb net $${fmt.num(p.arb_best_net_usd, 2)}`);
      if (p.equity_eth != null) bits.push(`${fmt.num(p.equity_eth, 4)} ETH`);
      if (p.day_realized_usd) bits.push(`24h real $${fmt.num(p.day_realized_usd, 2)}`);
      edge.textContent = bits.length ? bits.join(" · ") : "no live edge yet";
    }
    const led = $("fp-ledger");
    if (led) {
      const rows = (p.ledger || []).slice(0, 8);
      led.innerHTML = rows.length
        ? rows.map((e) => {
            const ago = Math.max(0, Math.floor((Date.now() / 1000) - (e.ts || 0)));
            const when = ago < 60 ? `${ago}s` : ago < 3600 ? `${Math.floor(ago / 60)}m` : `${Math.floor(ago / 3600)}h`;
            const cls = e.realized ? "green" : e.simulated ? "amber" : e.skipped ? "dim" : "";
            return `<tr>
              <td class="dim">${when}</td>
              <td>${e.kind || "?"}</td>
              <td class="${cls}">${e.stage || "?"}</td>
              <td class="${cls}">${e.profit_usd != null ? usd(e.profit_usd, true) : "—"}</td>
            </tr>`;
          }).join("")
        : `<tr><td colspan="4" class="dim">no broadcasts yet</td></tr>`;
    }

    const rows = Object.entries(s.funds || {}).map(([label, f]) => {
      const addr = (s.wallets || {})[label] || "";
      const t = f.weth != null ? `ETH ${fmt.num(f.eth, 4)} &middot; USDC ${fmt.num(f.usdc)} &middot; USDT ${fmt.num(f.usdt)} &middot; WETH ${fmt.num(f.weth, 4)}` : `ETH ${fmt.num(f.eth, 4)}`;
      const cls = f.eth > 0.001 ? "style=\"color:var(--green)\"" : "";
      const short = addr ? `${addr.slice(0, 6)}&hellip;${addr.slice(-4)}` : "--";
      return `<tr><td>${label.toUpperCase()}</td>` +
             `<td class="mono copy" data-addr="${addr}" title="click to copy">${short}</td>` +
             `<td ${cls}>${t}</td></tr>`;
    }).join("");
    $("funds-table").innerHTML = `<table><thead><tr><th>wallet</th><th>address</th><th>balances</th></tr></thead><tbody>${rows}</tbody></table>`;
  };

  document.addEventListener("click", (e) => {
    const c = e.target.closest(".copy");
    if (!c || !c.dataset.addr) return;
    navigator.clipboard.writeText(c.dataset.addr).then(() => {
      const old = c.textContent;
      c.textContent = "copied";
      setTimeout(() => { c.textContent = old; }, 1200);
    }).catch(() => {});
  });

  let mpFilter = "all";
  let mpLiveCache = [];

  const renderMevLive = () => {
    const live = mpFilter === "all"
      ? mpLiveCache
      : mpLiveCache.filter((t) => t.cls === mpFilter);
    const mevNote = $("mp-mev-note");
    if (mevNote) mevNote.textContent = `${live.length}/${mpLiveCache.length} · tip-sorted`;
    const mevEmpty = $("mp-mev-empty");
    if (mevEmpty) mevEmpty.style.display = live.length ? "none" : "block";
    const liveBody = $("mp-mev-live") && $("mp-mev-live").querySelector("tbody");
    if (!liveBody) return;
    liveBody.innerHTML = live.slice(0, 40).map((t) => {
      const tip = t.tip_gwei != null ? fmt.num(t.tip_gwei, 2) : "--";
      const gas = t.gas_gwei != null ? fmt.num(t.gas_gwei, 2) : "--";
      const tx = t.etherscan
        ? `<a href="${t.etherscan}" target="_blank" rel="noopener" style="color:var(--cyan)">${t.hash_short || (t.hash || "").slice(0, 10) + "…"}</a>`
        : `<span class="dim">${t.hash_short || "--"}</span>`;
      return `<tr>
        <td><span class="mp-cls ${t.cls || ""}">${t.cls || "?"}</span></td>
        <td title="${t.to || ""}"><b>${t.label || "--"}</b></td>
        <td class="mono dim" title="${t.sel || ""}">${t.sel_name || t.sel || "--"}</td>
        <td>${tip}</td>
        <td class="dim">${gas}</td>
        <td>${tx}</td>
      </tr>`;
    }).join("");
  };

  const filters = $("mp-filters");
  if (filters) {
    filters.addEventListener("click", (e) => {
      const btn = e.target.closest(".mp-f");
      if (!btn) return;
      mpFilter = btn.dataset.f || "all";
      filters.querySelectorAll(".mp-f").forEach((b) => b.classList.toggle("on", b === btn));
      renderMevLive();
    });
  }

  const updateMempool = (s) => {
    const m = s.mempool || {};
    const meta = m.meta || {};
    const hist = s.hist || {};
    $("mp-count").textContent = fmt.num(m.count, 0);
    const qEl = $("mp-queued");
    if (qEl) qEl.textContent = fmt.num(m.queued != null ? m.queued : meta.queued, 0);
    const liveN = meta.mev_live != null ? meta.mev_live : (m.mev_txs || []).length;
    const liveNEl = $("mp-mev-live-n");
    if (liveNEl) liveNEl.textContent = fmt.num(liveN, 0);
    const shareEl = $("mp-mev-share");
    if (shareEl) shareEl.textContent = (meta.mev_share_pct != null ? fmt.num(meta.mev_share_pct, 1) + "%" : "--");

    const pr = $("mp-pressure");
    if (pr) {
      const p = meta.pressure || "idle";
      pr.textContent = p;
      pr.className = "mp-pressure-badge " + p;
    }
    const metaEl = $("mp-meta");
    if (metaEl) {
      metaEl.innerHTML =
        `<span>method <b>${m.method || meta.method || "--"}</b></span>` +
        `<span>sampled <b>${fmt.num(meta.sampled, 0)}</b></span>` +
        (meta.content_age_s != null
          ? `<span>age <b>${fmt.num(meta.content_age_s, 0)}s</b></span>`
          : "") +
        `<span>contested <b style="color:${meta.contested ? "var(--red)" : "var(--dim)"}">${fmt.num(meta.contested, 0)}</b></span>`;
    }

    const mv = m.mev || {};
    const order = ["liq", "router", "spoke", "aave", "create", "other"];
    const totalMev = order.reduce((a, k) => a + (mv[k] || 0), 0) || 1;
    const track = $("mp-mix-track");
    if (track) {
      track.innerHTML = order.map((k) => {
        const n = mv[k] || 0;
        if (!n) return "";
        const pct = Math.max(0.4, 100 * n / totalMev);
        return `<span class="${k}" style="width:${pct}%" title="${k}: ${n}"></span>`;
      }).join("");
    }
    $("mp-mev").innerHTML = order.map((k) => {
      const n = mv[k] || 0;
      const pct = (100 * n / totalMev).toFixed(1);
      return `<span class="${k}">${k} <b>${fmt.num(n, 0)}</b> <i class="dim">${pct}%</i></span>`;
    }).join("");

    mpLiveCache = m.mev_txs || m.mev_samples || [];
    renderMevLive();

    const spoke = m.spoke_txs || [];
    const spokeNote = $("mp-spoke-note");
    if (spokeNote) spokeNote.textContent = `${spoke.length} spoke · ${(m.contested || []).length} contested`;
    const spokeEmpty = $("mp-spoke-empty");
    if (spokeEmpty) spokeEmpty.style.display = spoke.length ? "none" : "block";
    $("mp-spoke").querySelector("tbody").innerHTML = spoke.slice(0, 14).map((t) => {
      const hot = t.hot || /liquidat/i.test(t.name || "");
      const flag = hot ? `<span class="pill warn">LIQ</span>` : "";
      const user = t.user_short || (t.user ? t.user.slice(0, 10) + "…" : "--");
      return `<tr>
        <td style="color:${hot ? "var(--red)" : "var(--cyan)"}">${t.name || "?"}</td>
        <td class="mono" title="${t.user || ""}">${user}</td>
        <td class="args dim">${(t.args || []).join(", ")}</td>
        <td>${flag}</td>
      </tr>`;
    }).join("");

    const cont = $("mp-contested");
    if (cont) {
      const users = m.contested || [];
      cont.innerHTML = users.length
        ? users.map((u) =>
            `<span class="pill warn mono" title="${u}">${String(u).slice(0, 10)}…</span>`).join("")
        : `<span class="dim">no contested races</span>`;
    }

    const topBody = $("mp-top-table") && $("mp-top-table").querySelector("tbody");
    if (topBody) {
      const tops = m.top_to || [];
      topBody.innerHTML = tops.slice(0, 12).map((row) => {
        const obj = Array.isArray(row)
          ? { label: String(row[0]).slice(0, 10) + "…", kind: "other", count: row[1], pct: null, bar: 0, etherscan: `https://etherscan.io/address/${row[0]}`, mev: false }
          : row;
        const barCls = obj.mev || obj.kind === "router" || obj.kind === "lending" ? "mev" : (obj.kind === "token" ? "token" : "");
        const w = Math.max(2, Number(obj.bar) || 0);
        return `<tr>
          <td><span class="mp-kind ${obj.kind || "other"}">${obj.kind || "other"}</span></td>
          <td><a href="${obj.etherscan || "#"}" target="_blank" rel="noopener" style="color:var(--text)"><b>${obj.label || "?"}</b></a></td>
          <td><span class="mp-bar-track"><span class="mp-bar ${barCls}" style="width:${w}%"></span></span></td>
          <td>${fmt.num(obj.count, 0)}</td>
          <td class="dim">${obj.pct != null ? fmt.num(obj.pct, 1) + "%" : "--"}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="5" class="dim">waiting for txpool_content…</td></tr>`;
    }

    if (chartTx) {
      const pend = hist.tx_count || [];
      const queued = hist.tx_queued || [];
      const mevH = hist.tx_mev || [];
      const n = Math.max(pend.length, queued.length, mevH.length);
      const labels = [];
      const dPend = [], dQ = [], dM = [];
      for (let i = 0; i < n; i++) {
        const p = pend[i] || pend[pend.length - 1] || [0, 0];
        const q = queued[i] || [p[0], 0];
        const mvPt = mevH[i] || [p[0], 0];
        const ts = p[0] || q[0] || mvPt[0] || 0;
        labels.push(ts ? new Date((ts + 2 * 3600) * 1000).toISOString().slice(11, 19) : i);
        dPend.push(pend[i] ? pend[i][1] : null);
        dQ.push(queued[i] ? queued[i][1] : null);
        dM.push(mevH[i] ? mevH[i][1] : null);
      }
      chartTx.data.labels = labels;
      chartTx.data.datasets[0].data = dPend;
      chartTx.data.datasets[1].data = dQ;
      chartTx.data.datasets[2].data = dM;
      chartTx.update();
    }
  };

  let opFilter = "all";
  let opCache = [];
  const OP_PAIR_COLORS = ["#22c55e", "#22d3ee", "#f59e0b", "#a78bfa", "#ef4444", "#3b82f6", "#ec4899", "#14b8a6"];

  const oppHf = (o) => {
    const n = Number(o && o.hf);
    return isNaN(n) ? null : n / 1e18;
  };

  const hfUrgency = (hf) => {
    if (hf == null || hf >= 100) return { cls: "ok", label: "—", bar: 0 };
    if (hf < 1.0) return { cls: "crit", label: "liq", bar: 100 };
    if (hf < 1.05) return { cls: "hot", label: "hot", bar: 90 };
    if (hf < 1.1) return { cls: "warm", label: "warm", bar: 65 };
    if (hf < 1.25) return { cls: "ok", label: "ok", bar: 35 };
    return { cls: "ok", label: "safe", bar: 12 };
  };

  const hfClass = (hf) => {
    if (hf == null) return "op-hf-ok";
    if (hf < 1.0) return "op-hf-crit";
    if (hf < 1.05) return "op-hf-hot";
    if (hf < 1.1) return "op-hf-warm";
    return "op-hf-ok";
  };

  const renderOppsFeed = () => {
    const rows = opFilter === "all" ? opCache : opCache.filter((o) => {
      const hf = oppHf(o);
      if (opFilter === "edge") return !!o.edge;
      if (opFilter === "profit") return Number(o.profit_usd) > 0;
      if (opFilter === "hf095") return hf != null && hf < 0.95;
      if (opFilter === "hf1") return hf != null && hf < 1.0;
      return true;
    });
    const note = $("op-feed-note");
    if (note) note.textContent = `${rows.length}/${opCache.length} · profit-sorted`;
    const empty = $("opps-empty");
    if (empty) empty.style.display = rows.length ? "none" : "block";
    const body = $("opps-table") && $("opps-table").querySelector("tbody");
    if (!body) return;
    body.innerHTML = rows.slice(0, 60).map((o) => {
      const user = o.user || "";
      const short = user ? `${user.slice(0, 10)}…` : "--";
      const hf = oppHf(o);
      const hfCell = hf == null ? "--" : (hf >= 100 ? "∞" : hf.toFixed(3));
      const pair = `${o.coll_sym || "?"} → ${o.debt_sym || "?"}`;
      const flags = [];
      if (o.edge) flags.push(`<span class="op-flag edge">${o.edge}</span>`);
      if (Number(o.profit_usd) > 0) flags.push(`<span class="op-flag profit">+$</span>`);
      const links = user
        ? `<a class="op-link" href="https://etherscan.io/address/${user}" target="_blank" rel="noopener">↗</a>` +
          `<span class="mono copy op-link" data-addr="${user}" title="copy">copy</span>`
        : "";
      return `<tr>
        <td class="mono" title="${user}">${short}</td>
        <td class="${hfClass(hf)}">${hfCell}</td>
        <td><b>${pair}</b></td>
        <td style="color:var(--green)"><b>${fmt.usd(o.profit_usd)}</b></td>
        <td>${flags.join(" ") || `<span class="dim">—</span>`}</td>
        <td>${links}</td>
      </tr>`;
    }).join("");
  };

  const opFilters = $("op-filters");
  if (opFilters) {
    opFilters.addEventListener("click", (e) => {
      const btn = e.target.closest(".op-f");
      if (!btn) return;
      opFilter = btn.dataset.f || "all";
      opFilters.querySelectorAll(".op-f").forEach((b) => b.classList.toggle("on", b === btn));
      renderOppsFeed();
    });
  }

  const updateOpps = (s) => {
    const opps = s.opportunities || [];
    const wl = (s.watchlist || []).filter((w) => Number(w.hf) < 1e38);
    const m = s.opportunities_meta || {};
    const sweep = s.sweep_total != null ? s.sweep_total : m.sweep_total;
    opCache = opps.slice(0, 80);

    const count = m.count != null ? m.count : opps.length;
    const edgeN = m.edge_n != null ? m.edge_n : opps.filter((o) => o.edge).length;
    const best = m.best_profit != null
      ? m.best_profit
      : (opps.reduce((mx, o) => Math.max(mx, Number(o.profit_usd) || 0), 0) || null);
    const pressure = m.pressure || (count >= 5 ? "hot" : count >= 2 ? "busy" : count === 1 ? "quiet" : "idle");

    const badge = $("op-pressure");
    if (badge) {
      badge.textContent = pressure;
      badge.className = "op-pressure-badge " + pressure;
    }
    const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    setTxt("op-count", fmt.num(count, 0));
    setTxt("op-best", best != null && best > 0 ? fmt.usd(best) : (count ? fmt.usd(0) : "--"));
    setTxt("op-edge-n", fmt.num(edgeN, 0));
    setTxt("op-sweep", `${sweep != null ? fmt.num(sweep, 0) : "--"} / ${fmt.num(wl.length, 0)}`);

    const closest = wl[0];
    const closestHf = closest ? Number(closest.hf) / 1e18 : null;
    const closestEl = $("op-closest-hf");
    if (closestEl) {
      closestEl.textContent = closestHf == null ? "--" : (closestHf >= 100 ? "∞" : closestHf.toFixed(4));
      closestEl.className = "big " + (closestHf != null && closestHf < 1.05 ? "red" : closestHf != null && closestHf < 1.1 ? "amber" : "dim");
    }
    setTxt("op-closest-user", closest && closest.user
      ? `${closest.user.slice(0, 10)}…${closest.user.slice(-4)}`
      : "--");

    const urg = $("op-urgency");
    if (urg) {
      const buckets = [
        { label: "<1.00", n: wl.filter((w) => Number(w.hf) / 1e18 < 1).length },
        { label: "1–1.05", n: wl.filter((w) => { const h = Number(w.hf) / 1e18; return h >= 1 && h < 1.05; }).length },
        { label: "1.05–1.1", n: wl.filter((w) => { const h = Number(w.hf) / 1e18; return h >= 1.05 && h < 1.1; }).length },
        { label: "1.1+", n: wl.filter((w) => Number(w.hf) / 1e18 >= 1.1).length },
      ];
      const maxN = Math.max(1, ...buckets.map((b) => b.n));
      urg.innerHTML = buckets.map((b) =>
        `<div class="op-urg-row"><span>${b.label}</span>` +
        `<div class="op-urg-bar"><i style="width:${Math.round(100 * b.n / maxN)}%"></i></div>` +
        `<span>${b.n}</span></div>`).join("");
    }

    const meta = $("op-meta");
    if (meta) {
      const sweepBot = (s.bots || {}).sweep || {};
      meta.innerHTML =
        `<span>Σ profit <b style="color:var(--green)">${fmt.usd(m.sum_profit != null ? m.sum_profit : opps.reduce((a, o) => a + (Number(o.profit_usd) || 0), 0))}</b></span>` +
        `<span>watch <b>${fmt.num(wl.length, 0)}</b></span>` +
        (sweep != null ? `<span>tracked <b>${fmt.num(sweep, 0)}</b></span>` : "") +
        (sweepBot.status ? `<span>sweep <b>${sweepBot.status}</b></span>` : "") +
        (m.avg_hf != null ? `<span>avg HF <b>${Number(m.avg_hf).toFixed(3)}</b></span>` : "");
    }

    const mixSrc = (m.pair_mix && m.pair_mix.length)
      ? m.pair_mix
      : (() => {
          const counts = {};
          opps.forEach((o) => {
            const k = `${o.coll_sym || "?"}→${o.debt_sym || "?"}`;
            counts[k] = (counts[k] || 0) + 1;
          });
          const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
          return Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 8)
            .map(([pair, n]) => ({ pair, n, pct: Math.round(100 * n / total) }));
        })();
    const track = $("op-mix-track");
    const keys = $("op-mix-keys");
    if (track) {
      if (!mixSrc.length) {
        track.innerHTML = `<span style="width:100%;background:#334155"></span>`;
      } else {
        track.innerHTML = mixSrc.map((p, i) =>
          `<span style="width:${Math.max(4, p.pct || 0)}%;background:${OP_PAIR_COLORS[i % OP_PAIR_COLORS.length]}" title="${p.pair}"></span>`
        ).join("");
      }
    }
    if (keys) {
      keys.innerHTML = mixSrc.length
        ? mixSrc.map((p, i) =>
            `<span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${OP_PAIR_COLORS[i % OP_PAIR_COLORS.length]};margin-right:4px"></i>${p.pair} <b>${p.n}</b></span>`
          ).join("")
        : `<span class="dim">no liquidatable pairs</span>`;
    }

    renderOppsFeed();

    const watchNote = $("watch-note");
    if (watchNote) {
      watchNote.textContent = (sweep != null ? `${fmt.num(sweep, 0)} tracked` : "--") + " · lowest HF";
    }
    const wbody = $("watch-table") && $("watch-table").querySelector("tbody");
    if (wbody) {
      wbody.innerHTML = wl.map((w) => {
        const hf = Number(w.hf) / 1e18;
        const collUsd = Number(w.coll) / 1e26;
        const debtUsd = Number(w.debt) / 1e26;
        const hfCell = hf >= 100 ? "∞" : hf.toFixed(3);
        const urg = hfUrgency(hf);
        const user = w.user || "";
        const short = user ? `${user.slice(0, 10)}…` : "--";
        return `<tr>
          <td class="mono copy" data-addr="${user}" title="click to copy">${short}</td>
          <td class="${hfClass(hf)}">${hfCell}</td>
          <td>${fmt.usd(collUsd)}</td>
          <td>${fmt.usd(debtUsd)}</td>
          <td><span class="op-urg ${urg.cls}">${urg.label}</span></td>
        </tr>`;
      }).join("") || `<tr><td colspan="5" class="dim">--</td></tr>`;
    }
  };

  let cpFilter = "all";
  let cpCache = [];

  const renderCompFeed = () => {
    const rows = cpFilter === "all" ? cpCache : cpCache.filter((c) => {
      if (cpFilter === "miss") return !!c.missed_by_us;
      if (cpFilter === "edge") return !!c.edge;
      if (cpFilter === "profit") return (c.net_est_usd != null ? c.net_est_usd : c.est_profit_usd) > 0;
      if (cpFilter === "revert") return c.status === 0;
      return true;
    });
    const note = $("cp-feed-note");
    if (note) note.textContent = `${rows.length}/${cpCache.length} · newest first`;
    const empty = $("comp-empty");
    if (empty) empty.style.display = rows.length ? "none" : "block";
    const body = $("comp-table") && $("comp-table").querySelector("tbody");
    if (!body) return;
    body.innerHTML = rows.slice(0, 50).map((c) => {
      const pair = `${c.coll_sym || RESERVE_SYMS[+c.coll] || c.coll}→${c.debt_sym || RESERVE_SYMS[+c.debt] || c.debt}`;
      const searcher = c.searcher_short || (c.searcher ? c.searcher.slice(0, 10) : "--");
      const user = c.user_short || (c.user ? c.user.slice(0, 10) : "--");
      const net = c.net_est_usd;
      const netColor = net == null ? "var(--dim)" : net >= 0 ? "var(--green)" : "var(--red)";
      const flags = [];
      if (c.missed_by_us) flags.push(`<span class="cp-flag miss">miss</span>`);
      if (c.edge) flags.push(`<span class="cp-flag edge">edge</span>`);
      if (c.status === 0) flags.push(`<span class="cp-flag revert">revert</span>`);
      const tx = c.tx
        ? `<a href="https://etherscan.io/tx/${c.tx}" target="_blank" rel="noopener" style="color:var(--cyan)">${c.tx.slice(0, 8)}…</a>`
        : `<span class="dim">--</span>`;
      return `<tr>
        <td class="dim" title="blk ${c.block || "?"}">${fmt.age(c.ts)}</td>
        <td><b>${pair}</b></td>
        <td class="mono" title="${c.searcher || ""}">${searcher}…</td>
        <td class="mono dim" title="${c.user || ""}">${user}…</td>
        <td>${c.gas_cost_usd != null ? fmt.usd(c.gas_cost_usd) : "--"}</td>
        <td style="color:${c.est_profit_usd != null ? "var(--amber)" : "var(--dim)"}">${c.est_profit_usd != null ? fmt.usd(c.est_profit_usd) : "n/a"}</td>
        <td style="color:${netColor}"><b>${net != null ? fmt.usd(net) : "--"}</b></td>
        <td>${flags.join(" ") || `<span class="dim">—</span>`}</td>
        <td>${tx}</td>
      </tr>`;
    }).join("");
  };

  const cpFilters = $("cp-filters");
  if (cpFilters) {
    cpFilters.addEventListener("click", (e) => {
      const btn = e.target.closest(".cp-f");
      if (!btn) return;
      cpFilter = btn.dataset.f || "all";
      cpFilters.querySelectorAll(".cp-f").forEach((b) => b.classList.toggle("on", b === btn));
      renderCompFeed();
    });
  }

  const PAIR_COLORS = ["#ef4444", "#f59e0b", "#a78bfa", "#22d3ee", "#22c55e", "#3b82f6", "#ec4899", "#14b8a6"];

  const updateCompetitors = (s) => {
    const m = s.competitors_meta || {};
    const hist = s.hist || {};
    cpCache = (s.competitors || []).slice(0, 80);

    const pressure = m.pressure || "idle";
    const badge = $("cp-pressure");
    if (badge) {
      badge.textContent = pressure;
      badge.className = "cp-pressure-badge " + pressure;
    }
    const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    setTxt("cp-count", fmt.num(m.count_1h, 0));
    setTxt("cp-searchers", fmt.num(m.unique_searchers, 0));
    setTxt("cp-sum-est", fmt.usd(m.sum_est_profit));
    setTxt("cp-missed",
      m.missed_by_us
        ? `${fmt.num(m.missed_by_us, 0)}${m.miss_rate_pct ? ` · ${fmt.num(m.miss_rate_pct, 0)}%` : ""}`
        : "0");

    const meta = $("cp-meta");
    if (meta) {
      meta.innerHTML =
        (m.avg_gas != null ? `<span>avg gas <b>${fmt.num(m.avg_gas, 0)}</b></span>` : "") +
        `<span>Σ net <b style="color:${(m.sum_net_est || 0) >= 0 ? "var(--green)" : "var(--red)"}">${fmt.usd(m.sum_net_est)}</b></span>` +
        `<span>edge <b>${fmt.num(m.edge_n, 0)}</b></span>` +
        `<span>reverts <b>${fmt.num(m.revert_n, 0)}</b></span>` +
        `<span>spokes <b>${m.spokes || 0}</b></span>` +
        `<span>tracked <b>${fmt.num(m.total, 0)}</b></span>` +
        (m.last_block ? `<span>→ blk <b>${m.last_block}</b></span>` : "") +
        (m.status && m.status !== "ok" ? `<span style="color:var(--red)">${m.status}</span>` : "");
    }

    const mix = m.pair_mix || [];
    const track = $("cp-mix-track");
    const keys = $("cp-mix-keys");
    if (track) {
      track.innerHTML = mix.length
        ? mix.map((p, i) =>
            `<span style="width:${Math.max(2, p.pct || 0)}%;background:${PAIR_COLORS[i % PAIR_COLORS.length]}" title="${p.pair} ${p.n}"></span>`
          ).join("")
        : `<span style="width:100%;background:#1e293b"></span>`;
    }
    if (keys) {
      keys.innerHTML = mix.slice(0, 5).map((p, i) =>
        `<span><i style="display:inline-block;width:7px;height:7px;border-radius:2px;background:${PAIR_COLORS[i % PAIR_COLORS.length]};margin-right:4px"></i>${p.pair} <b>${p.n}</b></span>`
      ).join("") || `<span class="dim">no pairs yet</span>`;
    }

    const sBody = $("cp-searcher-table") && $("cp-searcher-table").querySelector("tbody");
    const sEmpty = $("cp-searcher-empty");
    const tops = m.top_searchers || [];
    if (sEmpty) sEmpty.style.display = tops.length ? "none" : "block";
    if (sBody) {
      sBody.innerHTML = tops.map((t, i) => {
        const pct = t.pct || 0;
        return `<tr>
          <td class="dim">${i + 1}</td>
          <td class="mono" title="${t.addr || ""}">${(t.short || (t.addr || "").slice(0, 10) || "--")}…</td>
          <td><div class="cp-bar-track"><div class="cp-bar" style="width:${Math.min(100, pct)}%"></div></div></td>
          <td>${fmt.num(t.n, 0)}</td>
          <td style="color:var(--amber)">${fmt.usd(t.est)}</td>
        </tr>`;
      }).join("");
    }

    const pNote = $("cp-pair-note");
    if (pNote) pNote.textContent = String(mix.length);
    const pBody = $("cp-pair-table") && $("cp-pair-table").querySelector("tbody");
    if (pBody) {
      pBody.innerHTML = mix.map((p) =>
        `<tr>
          <td><b>${p.pair}</b></td>
          <td><div class="cp-bar-track"><div class="cp-bar pair" style="width:${Math.min(100, p.pct || 0)}%"></div></div></td>
          <td>${fmt.num(p.n, 0)}</td>
          <td class="dim">${fmt.num(p.pct, 0)}%</td>
        </tr>`
      ).join("") || `<tr><td colspan="4" class="dim">no pair activity</td></tr>`;
    }

    if (chartComp) {
      const c1 = hist.comp_1h || [];
      const cm = hist.comp_missed || [];
      const n = Math.max(c1.length, cm.length);
      chartComp.data.labels = Array.from({ length: n }, (_, i) => i);
      chartComp.data.datasets[0].data = c1;
      chartComp.data.datasets[1].data = cm;
      chartComp.update();
    }

    renderCompFeed();
  };

  let arFilter = "all";
  let arLiveCache = [];
  let arNearCache = [];

  const arbFlag = (o) => {
    const bits = [];
    if (o.actionable) bits.push(`<span class="pill ok">LIVE</span>`);
    else if (o.net_usd != null && o.net_usd <= 0)
      bits.push(`<span class="pill warn">gas</span>`);
    if (o.cross_dex) bits.push(`<span class="pill accent">cross</span>`);
    if (o.sized) bits.push(`<span class="pill">sized</span>`);
    if (o.learned) bits.push(`<span class="pill accent">learn</span>`);
    if (o.etherscan)
      bits.push(`<a href="${o.etherscan}" target="_blank" rel="noopener">pool</a>`);
    return bits.join(" ") || "";
  };

  const venueBadge = (o) => {
    const v = (o.venue || "uni").toLowerCase();
    const cls = o.cross_dex || v.includes("+") ? "cross" : v;
    return `<span class="ar-venue ${cls}">${v}</span>`;
  };

  const renderArbFeed = () => {
    const nearMode = arFilter === "near";
    let rows = nearMode ? arNearCache : arLiveCache;
    if (!nearMode && arFilter !== "all") {
      rows = rows.filter((o) => {
        if (arFilter === "live") return !!o.actionable;
        if (arFilter === "cross") return !!o.cross_dex;
        if (arFilter === "uni") return (o.venue || "").includes("uni") && !o.cross_dex;
        if (arFilter === "sushi") return (o.venue || "").includes("sushi");
        return true;
      });
    }
    const note = $("ar-feed-note");
    if (note) {
      const base = nearMode ? arNearCache.length : arLiveCache.length;
      note.textContent = `${rows.length}/${base} · ${nearMode ? "near-miss" : "net-sorted"}`;
    }
    const empty = $("arb-empty");
    if (empty) empty.style.display = rows.length ? "none" : "block";
    const body = $("arb-table") && $("arb-table").querySelector("tbody");
    if (!body) return;
    body.innerHTML = rows.slice(0, 40).map((o) => {
      const netColor = o.net_usd == null ? "var(--dim)"
        : o.net_usd > 0 ? "var(--green)" : "var(--amber)";
      const gap = o.gap_usd != null ? o.gap_usd : o.net_usd;
      return `<tr>
        <td>${venueBadge(o)}</td>
        <td><b>${o.mid || "?"}</b></td>
        <td class="mono dim" title="${o.flash_full || ""}">${o.route || "--"}</td>
        <td>${fmt.num(o.hops || 2, 0)}</td>
        <td>${fmt.num(o.borrow_weth, 4)}</td>
        <td style="color:var(--amber)">${fmt.usd(o.profit_usd)}</td>
        <td class="dim">${fmt.usd(o.gas_usd)}</td>
        <td style="color:${netColor}"><b>${nearMode ? fmt.usd(gap) : fmt.usd(o.net_usd)}</b></td>
        <td>${arbFlag(o)}</td>
      </tr>`;
    }).join("");
  };

  const arFilters = $("ar-filters");
  if (arFilters) {
    arFilters.addEventListener("click", (e) => {
      const btn = e.target.closest(".ar-f");
      if (!btn) return;
      arFilter = btn.dataset.f || "all";
      arFilters.querySelectorAll(".ar-f").forEach((b) => b.classList.toggle("on", b === btn));
      renderArbFeed();
    });
  }

  const VENUE_COLORS = {
    uni: "#f472b6", sushi: "#f59e0b", "uni+sushi": "#a78bfa", "sushi+uni": "#a78bfa",
  };

  const updateArb = (s) => {
    const a = s.arb || {};
    const m = a.meta || {};
    const hist = s.hist || {};
    arLiveCache = a.opps || [];
    arNearCache = a.near || [];

    const pressure = m.pressure || "idle";
    const badge = $("ar-pressure");
    if (badge) {
      badge.textContent = pressure;
      badge.className = "ar-pressure-badge " + pressure;
    }
    const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    setTxt("ar-live", fmt.num(m.live, 0));
    setTxt("ar-actionable", fmt.num(m.actionable, 0));
    setTxt("ar-best-net", m.best_net_usd != null ? fmt.usd(m.best_net_usd) : "--");
    setTxt("ar-near-n", fmt.num(m.near, 0));

    const meta = $("arb-meta");
    if (meta) {
      const dexes = (m.dexes || []).join("+") || "—";
      meta.innerHTML =
        `<span>dexes <b>${dexes}</b></span>` +
        `<span>cross <b style="color:var(--violet)">${fmt.num(m.cross_dex, 0)}</b></span>` +
        (m.top_mid ? `<span>top <b>${m.top_mid}</b></span>` : "") +
        (m.gas_gwei != null ? `<span>gas <b>${fmt.num(m.gas_gwei, 2)} gwei</b></span>` : "") +
        (m.scan_ms != null ? `<span>scan <b>${fmt.num(m.scan_ms, 0)}ms</b></span>` : "") +
        (m.mode ? `<span>mode <b>${m.mode}</b></span>` : "") +
        (m.scan_block ? `<span>@ blk <b>${m.scan_block}</b></span>` : "") +
        ((m.preferred_mids || []).length
          ? `<span>learn <b>${m.preferred_mids.join(",")}</b></span>` : "") +
        (a.error ? `<span style="color:var(--red)">${a.error}</span>` : "");
    }

    const mix = m.venue_mix || [];
    const track = $("ar-mix-track");
    const keys = $("ar-mix-keys");
    if (track) {
      track.innerHTML = mix.length
        ? mix.map((p) => {
            const c = VENUE_COLORS[p.venue] || "#64748b";
            return `<span style="width:${Math.max(2, p.pct || 0)}%;background:${c}" title="${p.venue} ${p.n}"></span>`;
          }).join("")
        : `<span style="width:100%;background:#1e293b"></span>`;
    }
    if (keys) {
      keys.innerHTML = mix.slice(0, 5).map((p) => {
        const c = VENUE_COLORS[p.venue] || "#64748b";
        return `<span><i style="display:inline-block;width:7px;height:7px;border-radius:2px;background:${c};margin-right:4px"></i>${p.venue} <b>${p.n}</b></span>`;
      }).join("") || `<span class="dim">awaiting scan…</span>`;
    }

    const nearBody = $("arb-near-table") && $("arb-near-table").querySelector("tbody");
    const nearEmpty = $("arb-near-empty");
    if (nearEmpty) nearEmpty.style.display = arNearCache.length ? "none" : "block";
    if (nearBody) {
      nearBody.innerHTML = arNearCache.slice(0, 20).map((o) => {
        const gap = o.gap_usd != null ? o.gap_usd : o.net_usd;
        return `<tr>
          <td>${venueBadge(o)}</td>
          <td><b>${o.mid || "?"}</b></td>
          <td style="color:var(--amber)">${fmt.usd(o.profit_usd)}</td>
          <td style="color:var(--red)">${fmt.usd(gap)}</td>
          <td class="dim">${fmt.num(o.roi_bps, 1)}</td>
        </tr>`;
      }).join("");
    }

    const st = a.stats || {};
    const best = (st.best_profit_weth || 0) / 1e18;
    const cov = $("arb-stats");
    const covNote = $("ar-cov-note");
    if (covNote) covNote.textContent = (m.dexes || []).join("+") || "dex";
    if (cov) {
      const byDex = m.by_dex || st.by_dex || {};
      const dexBits = Object.entries(byDex).map(([k, v]) => `${k}:${v}`).join(" ");
      cov.innerHTML = [
        `<span>net <b>ethereum mainnet</b></span>`,
        `<span>routes <b>${fmt.num(m.routes != null ? m.routes : st.routes, 0)}</b></span>`,
        `<span>flash pools <b>${fmt.num(m.flash_pools != null ? m.flash_pools : st.flash_pools, 0)}</b></span>`,
        `<span>jobs <b>${fmt.num(m.jobs != null ? m.jobs : st.jobs, 0)}</b></span>`,
        `<span>screened <b>${fmt.num(m.screened != null ? m.screened : st.screened, 0)}</b></span>`,
        `<span>quoted <b>${fmt.num(m.quoted != null ? m.quoted : st.quoted, 0)}</b></span>`,
        (dexBits ? `<span>graph <b>${dexBits}</b></span>` : ""),
        (st.gas_gwei_live != null
          ? `<span>live gas <b>${fmt.num(st.gas_gwei_live, 3)} gwei</b></span>` : ""),
        `<span>best margin <b style="color:${best < 0 ? "var(--amber)" : "var(--green)"}">${fmt.num(best, 5)} WETH</b></span>`,
      ].filter(Boolean).join("");
    }

    if (chartArb) {
      const bn = hist.arb_best_net || [];
      const ac = hist.arb_actionable || [];
      const n = Math.max(bn.length, ac.length);
      chartArb.data.labels = Array.from({ length: n }, (_, i) => i);
      chartArb.data.datasets[0].data = bn;
      chartArb.data.datasets[1].data = ac;
      chartArb.update();
    }

    renderArbFeed();
  };

  const binCount = (obj, key) => {
    if (!obj) return 0;
    const v = obj[key];
    if (v != null && !Number.isNaN(+v)) return +v;
    const s = obj[String(key)];
    return (s != null && !Number.isNaN(+s)) ? +s : 0;
  };

  const updateIntel = (s) => {
    const i = s.intel || {};
    const b = i.brain || {};
    const ready = Number(i.readiness) || 0;
    const pressure = i.pressure || (ready >= 50 ? "hot" : ready >= 25 ? "busy" : ready >= 8 ? "quiet" : "idle");

    const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    setTxt("intel-records", fmt.num(i.records, 0));
    setTxt("intel-moves", fmt.num(i.moves, 0));
    setTxt("intel-block", i.last ? i.last.block : "--");
    setTxt("intel-act", b.act_prob != null ? (Number(b.act_prob) * 100).toFixed(1) + "%" : "--");
    setTxt("intel-steps", fmt.num(b.steps, 0));

    const expEl = $("intel-exp");
    if (expEl) {
      expEl.textContent = fmt.usd(b.exp_net_usd);
      expEl.classList.toggle("green", (b.exp_net_usd || 0) > 0);
      expEl.classList.toggle("amber", (b.exp_net_usd || 0) <= 0);
    }

    const badge = $("in-pressure");
    if (badge) {
      badge.textContent = pressure;
      badge.className = "in-pressure-badge " + pressure;
    }
    setTxt("in-advice", b.advice || i.advice || "warming up");
    setTxt("in-ready-pct", ready ? fmt.num(ready, 0) : "0");

    const meta = $("in-meta");
    if (meta) {
      const last = i.last || {};
      meta.innerHTML =
        `<span>moves <b>${fmt.num(i.moves, 0)}</b></span>` +
        `<span>block <b>${last.block != null ? last.block : "--"}</b></span>` +
        `<span>gas <b>${last.gas != null ? fmt.num(last.gas, 1) : "--"}</b></span>` +
        `<span>mempool <b>${fmt.num(last.mempool_txs, 0)}</b></span>` +
        (i.hours_source ? `<span>hours <b>${i.hours_source}</b></span>` : "");
    }

    if (gauge) {
      const filled = Math.max(0, Math.min(100, ready));
      const color = filled > 50 ? "#22c55e" : filled > 20 ? "#f59e0b" : "#ef4444";
      gauge.data.datasets[0].data = [filled, Math.max(0.001, 100 - filled)];
      gauge.data.datasets[0].backgroundColor = [color, "#1e293b"];
      gauge.update("none");
    }

    const hours = i.hours || {};
    const hourVals = Array.from({ length: 24 }, (_, h) => binCount(hours, h));
    const hourMax = Math.max(1, ...hourVals);
    if (chartHours) {
      chartHours.data.labels = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));
      chartHours.data.datasets[0].data = hourVals;
      chartHours.data.datasets[0].backgroundColor = hourVals.map((v) => {
        const t = v / hourMax;
        return t > 0.66 ? "#22d3ee" : t > 0.33 ? "#22d3eebb" : "#22d3ee66";
      });
      chartHours.update("none");
    }
    const hoursNote = $("in-hours-note");
    if (hoursNote) {
      const sum = hourVals.reduce((a, c) => a + c, 0);
      hoursNote.textContent = sum ? `Σ ${fmt.num(sum, 0)}` : "empty";
    }

    const dows = i.dows || {};
    const dowVals = Array.from({ length: 7 }, (_, d) => binCount(dows, d));
    const dowMax = Math.max(1, ...dowVals);
    if (chartDows) {
      chartDows.data.datasets[0].data = dowVals;
      chartDows.data.datasets[0].backgroundColor = dowVals.map((v) => {
        const t = v / dowMax;
        return t > 0.66 ? "#a78bfa" : t > 0.33 ? "#a78bfabb" : "#a78bfa66";
      });
      chartDows.update("none");
    }

    const mv = i.mev || {};
    const mevKeys = ["liq", "router", "spoke", "aave", "create"];
    const mevTotal = mevKeys.reduce((a, k) => a + (Number(mv[k]) || 0), 0) || 1;
    const track = $("in-mix-track");
    if (track) {
      track.innerHTML = mevKeys.map((k) => {
        const n = Number(mv[k]) || 0;
        const pct = Math.max(n ? 2 : 0, (n / mevTotal) * 100);
        return n ? `<span class="${k}" style="width:${pct}%" title="${k}: ${n}"></span>` : "";
      }).join("");
    }
    const mevEl = $("intel-mev");
    if (mevEl) {
      mevEl.innerHTML = mevKeys.map((k) =>
        `<span class="${k}">${k} <b>${fmt.num(mv[k], 0)}</b></span>`
      ).join("");
    }

    const bp = $("intel-brain");
    if (bp) {
      const confColor = (b.confidence || 0) > 0.4 ? "var(--green)" : "var(--amber)";
      bp.innerHTML =
        `<span>model <b>${(b.model || "DeepProfit").split(" ")[0]}</b></span>` +
        `<span>conf <b style="color:${confColor}">${fmt.num((b.confidence || 0) * 100, 0)}%</b></span>` +
        `<span>acc <b>${fmt.num((b.acc_ema || 0) * 100, 0)}%</b></span>` +
        `<span>loss <b>${fmt.num(b.loss_ema, 3)}</b></span>` +
        `<span>replay <b>${fmt.num(b.replay, 0)}</b></span>` +
        `<span>liq× <b>${fmt.num(b.min_liq_mult, 2)}</b></span>` +
        `<span>arb× <b>${fmt.num(b.min_arb_mult, 2)}</b></span>` +
        `<span>cadence× <b>${fmt.num(b.cadence_mult, 2)}</b></span>` +
        `<span>edge <b style="color:${b.prefer_edge ? "var(--cyan)" : "var(--dim)"}">${b.prefer_edge ? "prefer" : "off"}</b></span>`;
    }
    const brainNote = $("in-brain-note");
    if (brainNote) brainNote.textContent = b.prefer_edge ? "edge on" : "policy";

    if (chartIntelTrend && b.act_prob != null) {
      const p = Math.max(0, Math.min(1, Number(b.act_prob) || 0));
      intelTrendHist.push(p);
      if (intelTrendHist.length > 48) intelTrendHist.shift();
      chartIntelTrend.data.labels = intelTrendHist.map((_, idx) => idx);
      chartIntelTrend.data.datasets[0].data = intelTrendHist.slice();
      chartIntelTrend.update("none");
    }
  };

  let bcFilter = "all";
  let bcRowsCache = [];

  const bcKindBadge = (kind) => {
    const k = (kind || "?").toLowerCase();
    return `<span class="bc-kind ${k}">${k}</span>`;
  };
  const bcStageBadge = (stage) => {
    const st = (stage || "?").toLowerCase();
    const cls = st.replace(/[^a-z0-9_-]/g, "");
    return `<span class="bc-stage ${cls}">${stage || "?"}</span>`;
  };
  const bcDetail = (h) =>
    (h.user || h.flash || h.reason || h.why || h.msg || "").toString().slice(0, 56);

  const bcFilterRows = (rows) => {
    if (bcFilter === "all") return rows;
    return rows.filter((h) => {
      const kind = (h.kind || "").toLowerCase();
      const stage = (h.stage || "").toLowerCase();
      if (bcFilter === "liq") return kind === "liq";
      if (bcFilter === "arb") return kind === "arb";
      if (bcFilter === "skip") return kind === "skip" || stage === "skip";
      if (bcFilter === "sent") return stage === "sent" || stage === "ok";
      if (bcFilter === "sim") return stage === "simulated" || stage === "sim";
      return true;
    });
  };

  const renderBcHistory = () => {
    const tbl = $("bcast-table");
    const empty = $("bc-hist-empty");
    const note = $("bc-hist-note");
    if (!tbl) return;
    const filtered = bcFilterRows(bcRowsCache);
    if (note) note.textContent = `${filtered.length}/${bcRowsCache.length}`;
    if (empty) empty.style.display = filtered.length ? "none" : "block";
    const body = tbl.querySelector("tbody");
    if (!body) return;
    body.innerHTML = filtered.slice(0, 40).map((h) =>
      `<tr>
        <td>${fmt.ts(h.ts)}</td>
        <td>${bcKindBadge(h.kind)}</td>
        <td>${bcStageBadge(h.stage)}</td>
        <td class="args">${bcDetail(h)}</td>
      </tr>`
    ).join("");
  };

  const bcFilters = $("bc-filters");
  if (bcFilters) {
    bcFilters.addEventListener("click", (e) => {
      const btn = e.target.closest(".bc-f");
      if (!btn) return;
      bcFilter = btn.dataset.f || "all";
      bcFilters.querySelectorAll(".bc-f").forEach((b) => b.classList.toggle("on", b === btn));
      renderBcHistory();
    });
  }

  const updateBroadcast = (s) => {
    const bc = s.broadcast || {};
    const ready = bc.ready || {};
    const hist = bc.history || [];
    const skipped = bc.skipped || [];
    // Prefer backend summary; fall back to client counts so UI works either way.
    const sum = Object.assign({
      n_hist: hist.length,
      n_sent: hist.filter((h) => ["sent", "ok"].includes(String(h.stage || "").toLowerCase())).length,
      n_sim: hist.filter((h) => ["simulated", "sim"].includes(String(h.stage || "").toLowerCase())).length,
      n_skip: skipped.length + hist.filter((h) => String(h.stage || "").toLowerCase() === "skip").length,
      last_stage: hist[0] && hist[0].stage,
      last_kind: hist[0] && hist[0].kind,
      pressure: bc.pressure,
      label: null,
    }, bc.summary || {});
    const st = $("bcast-status");
    const rs = $("bcast-reasons");
    const near = $("bcast-near");
    if (!st || !rs) return;

    let pressure = sum.pressure || bc.pressure;
    let label = sum.label;
    if (!pressure) {
      if (!bc.enabled) { pressure = "idle"; label = label || "off"; }
      else if (bc.armed && (ready.liq || ready.arb)) { pressure = "hot"; label = label || "armed live"; }
      else if (bc.armed) { pressure = "elevated"; label = label || "armed · blocked"; }
      else if (bc.sim_only && (ready.liq || ready.arb)) { pressure = "quiet"; label = label || "sim ready"; }
      else if (bc.sim_only) { pressure = "busy"; label = label || "sim · blocked"; }
      else if (ready.liq || ready.arb) { pressure = "elevated"; label = label || "ready · disarm"; }
      else { pressure = "busy"; label = label || "blocked"; }
    }
    label = label || pressure || "idle";

    const badge = $("bc-pressure");
    if (badge) {
      badge.textContent = label;
      badge.className = "bc-pressure-badge " + pressure;
    }

    const setTxt = (id, v, cls) => {
      const el = $(id);
      if (!el) return;
      el.textContent = v;
      if (cls != null) el.className = "big " + cls;
    };
    setTxt("bc-liq", ready.liq ? "ready" : "blocked", ready.liq ? "green" : "red");
    setTxt("bc-arb", ready.arb ? "ready" : "blocked", ready.arb ? "green" : "red");
    setTxt("bc-dyn-liq", bc.dyn_min_liq != null ? "$" + fmt.num(bc.dyn_min_liq) : "--", "amber");
    setTxt("bc-dyn-arb", bc.dyn_min_arb != null ? "$" + fmt.num(bc.dyn_min_arb) : "--", "amber");
    const lastStage = sum.last_stage || "--";
    const okStage = ["sent", "ok", "simulated"].includes(String(lastStage).toLowerCase());
    setTxt("bc-last-stage", lastStage, okStage ? "green" : (lastStage === "--" ? "dim" : "amber"));

    st.innerHTML =
      `<span>mode <b>${bc.enabled ? "ON" : "OFF"}</b></span>` +
      `<span>peak <b style="color:${bc.peak_hour ? "var(--amber)" : "var(--dim)"}">${bc.peak_hour ? "YES" : "no"}</b></span>` +
      (bc.brain_advice ? `<span>brain <b style="color:var(--cyan)">${bc.brain_advice}</b></span>` : "") +
      `<span>sponsor <b>${fmt.num(bc.sponsor_target_eth, 3)} ETH</b></span>` +
      (bc.liq_contract ? `<span>liq <b>${bc.liq_contract.slice(0, 10)}…</b></span>` : "") +
      (bc.arb_contract ? `<span>arb <b>${bc.arb_contract.slice(0, 10)}…</b></span>` : "");

    const pills = $("bc-mode-pills");
    if (pills) {
      pills.innerHTML =
        `<span class="bc-pill ${bc.sim_only ? "on" : ""}">sim ${bc.sim_only ? "ON" : "off"}</span>` +
        `<span class="bc-pill ${bc.armed ? "live" : ""}">armed ${bc.armed ? "LIVE" : "no"}</span>` +
        `<span class="bc-pill ${bc.edge_bias ? "on" : ""}">edge ${bc.edge_bias ? "on" : "off"}</span>` +
        `<span class="bc-pill ${ready.liq && ready.arb ? "ok" : "warn"}">${ready.liq && ready.arb ? "gates clear" : "gates blocked"}</span>`;
    }

    const btnSim = $("btn-sim");
    const btnArm = $("btn-arm");
    const btnEdge = $("btn-edge");
    if (btnSim) {
      btnSim.classList.toggle("on-sim", !!bc.sim_only);
      btnSim.textContent = bc.sim_only ? "Sim ON" : "Sim-only";
    }
    if (btnArm) {
      btnArm.classList.toggle("on-arm", !!bc.armed);
      btnArm.textContent = bc.armed ? "Disarm LIVE" : "Arm LIVE 15m";
    }
    if (btnEdge) {
      btnEdge.classList.toggle("on-edge", !!bc.edge_bias);
      btnEdge.textContent = bc.edge_bias ? "Edge ON" : "Edge bias";
    }

    const summary = $("bc-summary");
    if (summary) {
      summary.innerHTML =
        `<span>hist <b>${fmt.num(sum.n_hist, 0)}</b></span>` +
        `<span>sent <b style="color:var(--green)">${fmt.num(sum.n_sent, 0)}</b></span>` +
        `<span>sim <b style="color:var(--cyan)">${fmt.num(sum.n_sim, 0)}</b></span>` +
        `<span>skips <b style="color:var(--amber)">${fmt.num(sum.n_skip, 0)}</b></span>` +
        (sum.last_kind ? `<span>last <b>${sum.last_kind}</b></span>` : "");
    }

    const readyNote = $("bc-ready-note");
    if (readyNote) {
      readyNote.textContent = (ready.liq || ready.arb) ? "partial/ready" : "blocked";
    }
    rs.innerHTML = (ready.reasons || []).length
      ? ready.reasons.map((r) => `<span style="color:var(--amber)">${r}</span>`).join("")
      : "<span style=\"color:var(--green)\">ready to submit</span>";

    if (near) {
      near.innerHTML = (bc.near_miss_hints || []).length
        ? bc.near_miss_hints.map((h) =>
            `<span>${h.mid} fee${h.fee} n=${h.n} best <b>${fmt.num(h.best_weth, 5)}</b> WETH</span>`
          ).join("")
        : "<span class=\"dim\">no near-miss data yet</span>";
    }

    bcRowsCache = []
      .concat(hist)
      .concat(skipped.map((x) => ({
        ...x,
        kind: x.kind || "skip",
        stage: x.stage || x.why || "skip",
      })));
    bcRowsCache.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    renderBcHistory();

    const recentBody = $("bc-recent-table") && $("bc-recent-table").querySelector("tbody");
    const recentEmpty = $("bc-recent-empty");
    const recentNote = $("bc-recent-note");
    const histOnly = hist.slice(0, 8);
    if (recentNote) recentNote.textContent = histOnly.length ? `${histOnly.length}` : "last";
    if (recentEmpty) recentEmpty.style.display = histOnly.length ? "none" : "block";
    if (recentBody) {
      recentBody.innerHTML = histOnly.map((h) =>
        `<tr>
          <td>${bcKindBadge(h.kind)}</td>
          <td>${bcStageBadge(h.stage)}</td>
          <td class="args">${bcDetail(h)}</td>
        </tr>`
      ).join("");
    }
  };

  const postControl = async (body) => {
    try {
      const r = await fetch("/api/control", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      return await r.json();
    } catch (e) {
      return null;
    }
  };

  const updatePrices = (s) => {
    const gasEl = $("mc-gas");
    if (gasEl) {
      gasEl.textContent = s.gas_gwei != null ? fmt.num(s.gas_gwei, 2) + " gwei" : "--";
      gasEl.className = "big " + (s.gas_class === "hot" ? "red" : s.gas_class === "normal" ? "amber" : "green");
    }
    if (s.eth_price_usd != null && $("mc-eth-price") && !window.__mcCandlePrice) {
      $("mc-eth-price").textContent = fmt.usd(s.eth_price_usd);
    }
    const meta = $("mc-meta");
    if (meta) {
      const bits = [];
      if (s.eth_price_usd != null) bits.push(`<span>oracle eth <b>${fmt.usd(s.eth_price_usd)}</b></span>`);
      if (s.gas_gwei != null) bits.push(`<span>gas <b>${fmt.num(s.gas_gwei, 2)} gwei</b></span>`);
      bits.push(`<span>tf <b>${(window.__mcInterval || "1h").toUpperCase()}</b></span>`);
      meta.innerHTML = bits.join("");
    }
    const res = s.prices.reserves || {};
    const deltaMap = {};
    (s.prices.deltas || []).forEach(([rid, sym, pct]) => (deltaMap[rid] = pct));
    $("res-delta").textContent = Object.keys(deltaMap).length ? Object.entries(deltaMap).map(([r, p]) => `${RESERVE_SYMS[+r]} ${p > 0 ? "+" : ""}${p}%`).join(" ") : "stable";
    $("reserves-list").innerHTML = Object.entries(res).slice(0, 14).map(([rid, v]) => {
      const sym = RESERVE_SYMS[+rid] || rid;
      const d = deltaMap[rid];
      return `<span>${sym} <b>${fmt.num(v / 1e8, 2)}</b>${d ? `<i style="color:${d < 0 ? "var(--red)" : "var(--green)"}"> (${d > 0 ? "+" : ""}${d}%)</i>` : ""}</span>`;
    }).join("");
  };

  const pushSeries = (chart, arr) => {
    if (!chart || !Array.isArray(arr)) return;
    const len = arr.length;
    chart.data.labels = arr.map((p, i) => i);
    chart.data.datasets[0].data = arr.map((p) => p[1]);
    chart.update();
    void len;
  };

  /* ------------------------------------------------ Activity Log (al-*) */
  const LOG_MAX = 250;
  const AL_PREF_CATS = ["mempool", "sweep", "competitor", "arb", "broadcast", "intel", "funds", "price", "bot"];
  let logLines = [];
  let logSessionTotal = 0;
  let logLevelFilter = "all";   /* all | warn | error | info */
  let logCatFilter = null;      /* null = any, else cat string */
  let logSearch = "";
  let logAutoScroll = true;
  let logCatsSeen = new Set();

  const escapeHtml = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const logLineMatches = (l) => {
    if (!l) return false;
    const lvl = (l.level || "info").toLowerCase();
    if (logLevelFilter === "warn" && lvl !== "warn") return false;
    if (logLevelFilter === "error" && lvl !== "error") return false;
    if (logLevelFilter === "info" && lvl !== "info" && lvl !== "ok" && lvl !== "money") return false;
    if (logCatFilter && (l.cat || "") !== logCatFilter) return false;
    if (logSearch) {
      const q = logSearch.toLowerCase();
      const hay = ((l.msg || "") + " " + (l.cat || "") + " " + (l.level || "")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  };

  const logLineHtml = (l) => {
    const lvl = (l.level || "info").toLowerCase();
    const cat = l.cat || "?";
    return `<div class="ln ${escapeHtml(lvl)}" data-lvl="${escapeHtml(lvl)}" data-cat="${escapeHtml(cat)}">` +
      `<span class="t">${fmt.ts(l.ts)}</span>` +
      `<span class="al-lvl-badge ${escapeHtml(lvl)}">${escapeHtml(lvl)}</span>` +
      `<span class="al-cat-pill" title="${escapeHtml(cat)}">${escapeHtml(cat)}</span>` +
      `<span class="m">${escapeHtml(l.msg)}</span></div>`;
  };

  const alScrollToBottom = () => {
    const el = $("log");
    if (!el || !logAutoScroll) return;
    el.scrollTop = el.scrollHeight;
  };

  const rebuildAlCatChips = () => {
    const root = $("al-cat-filters");
    if (!root) return;
    const fromData = [...logCatsSeen];
    const ordered = [];
    AL_PREF_CATS.forEach((c) => { if (logCatsSeen.has(c) && !ordered.includes(c)) ordered.push(c); });
    fromData.sort().forEach((c) => { if (!ordered.includes(c)) ordered.push(c); });
    const cats = ordered.slice(0, 12);
    root.innerHTML = cats.map((c) =>
      `<button type="button" class="al-f cat${logCatFilter === c ? " on" : ""}" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`
    ).join("");
  };

  const updateAlHero = () => {
    const now = Math.floor(Date.now() / 1000);
    const n = logLines.length;
    const byLvl = { info: 0, warn: 0, error: 0, ok: 0, money: 0 };
    const byCat = {};
    let recent = 0;
    logLines.forEach((l) => {
      const lvl = (l.level || "info").toLowerCase();
      if (byLvl[lvl] != null) byLvl[lvl]++;
      else byLvl.info++;
      const c = l.cat || "?";
      byCat[c] = (byCat[c] || 0) + 1;
      if (l.ts && now - l.ts <= 60) recent++;
    });
    const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    setTxt("al-lines", String(logSessionTotal || n));
    setTxt("al-rate", String(recent));
    const last = n ? logLines[n - 1] : null;
    setTxt("al-last", last ? fmt.age(last.ts) : "--");
    setTxt("al-errors", String(byLvl.error || 0));
    setTxt("al-n-info", String(byLvl.info || 0));
    setTxt("al-n-warn", String(byLvl.warn || 0));
    setTxt("al-n-error", String(byLvl.error || 0));
    setTxt("al-n-ok", String((byLvl.ok || 0) + (byLvl.money || 0)));

    const pr = $("al-pressure");
    if (pr) {
      let p = "idle";
      if (byLvl.error > 0 && last && now - last.ts < 120 && (last.level || "") === "error") p = "hot";
      else if (byLvl.error > 0 || recent >= 20) p = "busy";
      else if (recent > 0 || n > 0) p = "quiet";
      pr.textContent = p;
      pr.className = "al-pressure-badge " + p;
    }

    const top = Object.entries(byCat).sort((a, b) => b[1] - a[1]).slice(0, 6);
    const totalCat = top.reduce((s, [, v]) => s + v, 0) || 1;
    const track = $("al-mix-track");
    if (track) {
      track.innerHTML = top.map(([, v], i) =>
        `<span class="c${i}" style="width:${Math.max(2, (v / totalCat) * 100)}%"></span>`).join("");
    }
    const kv = $("al-cats-kv");
    if (kv) {
      kv.innerHTML = top.length
        ? top.map(([c, v]) => `<span>${escapeHtml(c)} <b>${v}</b></span>`).join("")
        : `<span class="dim">no events yet</span>`;
    }
    const meta = $("al-meta");
    if (meta) {
      const bits = [];
      bits.push(`<span>buffer <b>${n}</b>/${LOG_MAX}</span>`);
      if (logCatFilter) bits.push(`<span>cat <b>${escapeHtml(logCatFilter)}</b></span>`);
      if (logLevelFilter !== "all") bits.push(`<span>lvl <b>${escapeHtml(logLevelFilter)}</b></span>`);
      if (logSearch) bits.push(`<span>q <b>${escapeHtml(logSearch)}</b></span>`);
      meta.innerHTML = bits.join("");
    }
  };

  const renderLogFeed = () => {
    const el = $("log");
    if (!el) return;
    const visible = logLines.filter(logLineMatches);
    const shown = $("al-shown");
    if (shown) shown.textContent = `${visible.length} shown`;
    if (!visible.length) {
      el.innerHTML = `<p class="empty">no matching log lines</p>`;
      return;
    }
    el.innerHTML = visible.map(logLineHtml).join("");
    alScrollToBottom();
  };

  const ingestLogLines = (lines, { reset } = {}) => {
    if (reset) {
      logLines = [];
      logCatsSeen = new Set();
      logSessionTotal = 0;
    }
    (lines || []).forEach((l) => {
      if (!l) return;
      logLines.push(l);
      logSessionTotal++;
      if (l.cat) logCatsSeen.add(l.cat);
    });
    if (logLines.length > LOG_MAX) logLines = logLines.slice(-LOG_MAX);
    rebuildAlCatChips();
    updateAlHero();
    renderLogFeed();
  };

  const appendLogLine = (l) => {
    if (!l) return;
    logLines.push(l);
    logSessionTotal++;
    if (l.cat && !logCatsSeen.has(l.cat)) {
      logCatsSeen.add(l.cat);
      rebuildAlCatChips();
    }
    if (logLines.length > LOG_MAX) {
      logLines = logLines.slice(-LOG_MAX);
      /* full re-render when trimming so DOM stays in sync */
      updateAlHero();
      renderLogFeed();
      return;
    }
    updateAlHero();
    const el = $("log");
    if (!el) return;
    if (!logLineMatches(l)) {
      const shown = $("al-shown");
      if (shown) shown.textContent = `${el.querySelectorAll(".ln").length} shown`;
      return;
    }
    const empty = el.querySelector(".empty");
    if (empty) empty.remove();
    el.insertAdjacentHTML("beforeend", logLineHtml(l));
    while (el.querySelectorAll(".ln").length > LOG_MAX) {
      const first = el.querySelector(".ln");
      if (first) first.remove();
      else break;
    }
    const shown = $("al-shown");
    if (shown) shown.textContent = `${el.querySelectorAll(".ln").length} shown`;
    alScrollToBottom();
  };

  const updateLog = (s, lines) => {
    const src = lines || (s && s.log) || [];
    ingestLogLines(src, { reset: true });
    /* optional server meta enrichment */
    const meta = s && s.log_meta;
    if (meta && meta.session_total != null) {
      logSessionTotal = Math.max(logSessionTotal, meta.session_total);
      const el = $("al-lines");
      if (el) el.textContent = String(logSessionTotal);
    }
  };

  const bindAlControls = () => {
    const lvlRoot = $("al-level-filters");
    if (lvlRoot && !lvlRoot.__bound) {
      lvlRoot.__bound = true;
      lvlRoot.addEventListener("click", (ev) => {
        const btn = ev.target.closest(".al-f");
        if (!btn) return;
        logLevelFilter = btn.getAttribute("data-f") || "all";
        logCatFilter = null;
        lvlRoot.querySelectorAll(".al-f").forEach((b) => b.classList.toggle("on", b === btn));
        $("al-cat-filters")?.querySelectorAll(".al-f").forEach((b) => b.classList.remove("on"));
        updateAlHero();
        renderLogFeed();
      });
    }
    const catRoot = $("al-cat-filters");
    if (catRoot && !catRoot.__bound) {
      catRoot.__bound = true;
      catRoot.addEventListener("click", (ev) => {
        const btn = ev.target.closest(".al-f");
        if (!btn) return;
        const cat = btn.getAttribute("data-cat");
        if (logCatFilter === cat) {
          logCatFilter = null;
          btn.classList.remove("on");
        } else {
          logCatFilter = cat;
          catRoot.querySelectorAll(".al-f").forEach((b) => b.classList.toggle("on", b === btn));
        }
        updateAlHero();
        renderLogFeed();
      });
    }
    const search = $("al-search");
    if (search && !search.__bound) {
      search.__bound = true;
      let t = null;
      search.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(() => {
          logSearch = (search.value || "").trim();
          updateAlHero();
          renderLogFeed();
        }, 120);
      });
    }
    const autoBtn = $("al-autoscroll");
    if (autoBtn && !autoBtn.__bound) {
      autoBtn.__bound = true;
      autoBtn.addEventListener("click", () => {
        logAutoScroll = !logAutoScroll;
        autoBtn.classList.toggle("on", logAutoScroll);
        autoBtn.classList.toggle("paused", !logAutoScroll);
        autoBtn.textContent = logAutoScroll ? "auto ↓" : "paused";
        if (logAutoScroll) alScrollToBottom();
      });
    }
    const feed = $("log");
    if (feed && !feed.__scrollBound) {
      feed.__scrollBound = true;
      feed.addEventListener("scroll", () => {
        const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 48;
        if (!nearBottom && logAutoScroll) {
          logAutoScroll = false;
          const btn = $("al-autoscroll");
          if (btn) {
            btn.classList.remove("on");
            btn.classList.add("paused");
            btn.textContent = "paused";
          }
        } else if (nearBottom && !logAutoScroll) {
          logAutoScroll = true;
          const btn = $("al-autoscroll");
          if (btn) {
            btn.classList.add("on");
            btn.classList.remove("paused");
            btn.textContent = "auto ↓";
          }
        }
      });
    }
  };

  /* ------------------------------------------------ candlesticks (lightweight-charts) — ETH/USD only */
  const MC_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];
  const MC_LIMITS = { "1m": 240, "5m": 240, "15m": 200, "1h": 180, "4h": 180, "1d": 180 };
  let ethChart = null, ethSeries = null;
  let mcInterval = "1h";
  let mcLoadSeq = 0;
  window.__mcInterval = mcInterval;
  const candleStyle = {
    upColor: "#22c55e", downColor: "#ef4444",
    borderUpColor: "#22c55e", borderDownColor: "#ef4444",
    wickUpColor: "#22c55e", wickDownColor: "#ef4444",
  };

  const setMcChg = (pct) => {
    const el = $("mc-eth-chg");
    if (!el || pct == null || isNaN(pct)) return;
    const sign = pct > 0 ? "+" : "";
    el.textContent = sign + pct.toFixed(2) + "%";
    el.className = "big " + (pct > 0 ? "green" : pct < 0 ? "red" : "dim");
  };

  const initCandles = () => {
    const el = $("eth-candles");
    if (!el) return;
    el.querySelector(".lwc-msg")?.remove();
    const h = el.clientHeight || 320;
    ethChart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 600, height: h,
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#64748b", fontFamily: "JetBrains Mono, monospace" },
      grid: { vertLines: { color: "#1e293b55" }, horzLines: { color: "#1e293b55" } },
      rightPriceScale: { borderColor: "#334155" },
      timeScale: { borderColor: "#334155", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    ethSeries = ethChart.addCandlestickSeries(candleStyle);
    const resize = () => {
      const box = $("eth-candles");
      if (!box || !ethChart) return;
      ethChart.applyOptions({ width: box.clientWidth || 600, height: box.clientHeight || 320 });
    };
    window.addEventListener("resize", resize);
    requestAnimationFrame(resize);
  };

  const loadEthChg24h = async () => {
    try {
      const r = await fetch("/api/klines?symbol=ETHUSDT&interval=1h&limit=25");
      const kl = await r.json();
      if (!Array.isArray(kl) || kl.length < 2) return;
      const open = +kl[0][1];
      const close = +kl[kl.length - 1][4];
      if (!open) return;
      setMcChg(((close - open) / open) * 100);
    } catch (err) { /* keep last */ }
  };

  const loadKlines = async (interval) => {
    if (!ethSeries) return;
    const tf = MC_INTERVALS.includes(interval) ? interval : "1h";
    const seq = ++mcLoadSeq;
    const limit = MC_LIMITS[tf] || 180;
    const msg = $("eth-candles")?.querySelector(".lwc-msg");
    if (msg) msg.textContent = "loading " + tf + "…";
    try {
      const r = await fetch(`/api/klines?symbol=ETHUSDT&interval=${encodeURIComponent(tf)}&limit=${limit}`);
      const kl = await r.json();
      if (seq !== mcLoadSeq) return;
      if (!Array.isArray(kl) || !kl.length) {
        if (msg) msg.textContent = "no candle data";
        return;
      }
      const candles = kl.map((k) => ({
        time: Math.floor(k[0] / 1000),
        open: +k[1], high: +k[2], low: +k[3], close: +k[4],
      }));
      ethSeries.setData(candles);
      if (ethChart) ethChart.timeScale().fitContent();
      const last = candles[candles.length - 1];
      const priceEl = $("mc-eth-price");
      if (priceEl && last) {
        priceEl.textContent = "$" + last.close.toLocaleString(undefined, { maximumFractionDigits: 2 });
        window.__mcCandlePrice = true;
      }
      const legacy = $("eth-last");
      if (legacy && last) legacy.textContent = "$" + last.close.toFixed(2);
      const upd = $("mc-updated");
      if (upd) upd.textContent = new Date(Date.now() + SAST_OFFSET * 1000).toISOString().slice(11, 19);
      msg?.remove();
    } catch (err) {
      if (seq === mcLoadSeq && msg) msg.textContent = "candle fetch failed";
    }
  };

  const setMcInterval = (tf) => {
    if (!MC_INTERVALS.includes(tf)) return;
    mcInterval = tf;
    window.__mcInterval = tf;
    document.querySelectorAll("#mc-tf .mc-f").forEach((b) => {
      b.classList.toggle("on", b.getAttribute("data-tf") === tf);
    });
    loadKlines(tf);
  };

  const bindMcTf = () => {
    const root = $("mc-tf");
    if (!root || root.__bound) return;
    root.__bound = true;
    root.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".mc-f");
      if (!btn) return;
      setMcInterval(btn.getAttribute("data-tf"));
    });
  };

  /* ------------------------------------------------ chain tabs ETH | SOL */
  const TAB_KEY = "toni-chain-tab";
  let activeTab = "eth";
  let solChart = null, solSeries = null;
  let solInterval = "1h";
  let solLoadSeq = 0;
  let solWorkspaceReady = false;
  let solRefreshTimer = null;

  const resizeEthChart = () => {
    const box = $("eth-candles");
    if (!box || !ethChart) return;
    ethChart.applyOptions({ width: box.clientWidth || 600, height: box.clientHeight || 320 });
  };

  const resizeSolChart = () => {
    const box = $("sol-candles");
    if (!box || !solChart) return;
    solChart.applyOptions({ width: box.clientWidth || 600, height: box.clientHeight || 320 });
  };

  const setSolChg = (pct) => {
    const apply = (el) => {
      if (!el || pct == null || isNaN(pct)) return;
      const sign = pct > 0 ? "+" : "";
      el.textContent = sign + pct.toFixed(2) + "%";
      el.className = "big " + (pct > 0 ? "green" : pct < 0 ? "red" : "dim");
    };
    apply($("sol-mc-chg"));
    apply($("sol-chg"));
  };

  const sastClock = () => new Date(Date.now() + SAST_OFFSET * 1000).toISOString().slice(11, 19);

  const pushSolAct = (msg) => {
    const feed = $("sol-activity");
    if (!feed) return;
    $("sol-act-empty")?.remove();
    const row = document.createElement("div");
    row.className = "ln";
    row.innerHTML = `<span class="t">${sastClock()}</span><span class="m"></span>`;
    row.querySelector(".m").textContent = msg;
    feed.prepend(row);
    while (feed.children.length > 40) feed.lastElementChild.remove();
  };

  const initSolCandles = () => {
    const el = $("sol-candles");
    if (!el || solChart || !window.LightweightCharts) return;
    el.querySelector(".lwc-msg")?.remove();
    const h = el.clientHeight || 320;
    solChart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 600, height: h,
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#64748b", fontFamily: "JetBrains Mono, monospace" },
      grid: { vertLines: { color: "#1e293b55" }, horzLines: { color: "#1e293b55" } },
      rightPriceScale: { borderColor: "#334155" },
      timeScale: { borderColor: "#334155", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    solSeries = solChart.addCandlestickSeries(candleStyle);
    window.addEventListener("resize", resizeSolChart);
    requestAnimationFrame(resizeSolChart);
  };

  const loadSolChg24h = async () => {
    try {
      const r = await fetch("/api/klines?symbol=SOLUSDT&interval=1h&limit=25");
      const kl = await r.json();
      if (!Array.isArray(kl) || kl.length < 2) return;
      const open = +kl[0][1];
      const close = +kl[kl.length - 1][4];
      if (!open) return;
      setSolChg(((close - open) / open) * 100);
    } catch (err) { /* keep last */ }
  };

  const loadSolKlines = async (interval) => {
    if (!solSeries) return;
    const tf = MC_INTERVALS.includes(interval) ? interval : "1h";
    const seq = ++solLoadSeq;
    const limit = MC_LIMITS[tf] || 180;
    const box = $("sol-candles");
    let msg = box?.querySelector(".lwc-msg");
    if (box && !msg) {
      msg = document.createElement("span");
      msg.className = "lwc-msg";
      box.appendChild(msg);
    }
    if (msg) msg.textContent = "loading " + tf + "…";
    try {
      const r = await fetch(`/api/klines?symbol=SOLUSDT&interval=${encodeURIComponent(tf)}&limit=${limit}`);
      const kl = await r.json();
      if (seq !== solLoadSeq) return;
      if (!Array.isArray(kl) || !kl.length) {
        if (msg) msg.textContent = "no candle data";
        return;
      }
      const candles = kl.map((k) => ({
        time: Math.floor(k[0] / 1000),
        open: +k[1], high: +k[2], low: +k[3], close: +k[4],
      }));
      solSeries.setData(candles);
      if (solChart) solChart.timeScale().fitContent();
      const last = candles[candles.length - 1];
      if (last) {
        const px = "$" + last.close.toLocaleString(undefined, { maximumFractionDigits: 4 });
        const mcPx = $("sol-mc-price");
        const heroPx = $("sol-price");
        if (mcPx) mcPx.textContent = px;
        if (heroPx) heroPx.textContent = px;
      }
      const upd = $("sol-mc-updated");
      if (upd) upd.textContent = sastClock();
      const heroUpd = $("sol-updated");
      if (heroUpd) heroUpd.textContent = sastClock();
      const meta = $("sol-mc-meta");
      if (meta) meta.innerHTML = `<span>pair <b>SOLUSDT</b></span><span>tf <b>${tf}</b></span><span>bars <b>${candles.length}</b></span>`;
      const heroMeta = $("sol-meta");
      if (heroMeta) heroMeta.innerHTML = `<span>source <b>Binance</b></span><span>pair <b>SOL/USD</b></span><span>network <b>Solana mainnet</b></span>`;
      msg?.remove();
    } catch (err) {
      if (seq === solLoadSeq && msg) msg.textContent = "candle fetch failed";
    }
  };

  const setSolInterval = (tf) => {
    if (!MC_INTERVALS.includes(tf)) return;
    solInterval = tf;
    const label = $("sol-mc-tf");
    if (label) label.textContent = tf === "1d" ? "1D" : tf.toUpperCase();
    document.querySelectorAll("#sol-mc-tf-btns .mc-f").forEach((b) => {
      b.classList.toggle("on", b.getAttribute("data-tf") === tf);
    });
    loadSolKlines(tf);
  };

  const bindSolTf = () => {
    const root = $("sol-mc-tf-btns");
    if (!root || root.__bound) return;
    root.__bound = true;
    root.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".mc-f");
      if (!btn) return;
      setSolInterval(btn.getAttribute("data-tf"));
    });
  };

  const bindSolNotes = () => {
    const add = $("sol-note-add");
    const clear = $("sol-note-clear");
    const input = $("sol-note-input");
    if (add && !add.__bound) {
      add.__bound = true;
      add.addEventListener("click", () => {
        const t = (input?.value || "").trim();
        if (!t) return;
        pushSolAct(t);
        if (input) input.value = "";
      });
    }
    if (input && !input.__bound) {
      input.__bound = true;
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") $("sol-note-add")?.click();
      });
    }
    if (clear && !clear.__bound) {
      clear.__bound = true;
      clear.addEventListener("click", () => {
        const feed = $("sol-activity");
        if (!feed) return;
        feed.innerHTML = `<div class="empty" id="sol-act-empty">No SOL notes yet — candles + RPC status will log here</div>`;
      });
    }
  };

  const refreshSolStatus = async () => {
    const badge = $("sol-net-pressure");
    const empty = $("sol-net-empty");
    try {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 6000);
      const r = await fetch("/api/sol/status", { signal: ctrl.signal });
      clearTimeout(to);
      const d = await r.json();
      const slotTxt = d.slot != null ? fmt.num(d.slot, 0) : "--";
      if ($("sol-slot")) $("sol-slot").textContent = slotTxt;
      if ($("sol-net-slot")) $("sol-net-slot").textContent = slotTxt;
      if ($("sol-net-epoch")) $("sol-net-epoch").textContent = d.epoch != null ? fmt.num(d.epoch, 0) : "--";
      if ($("sol-net-slot-idx")) $("sol-net-slot-idx").textContent = d.slot_index != null ? fmt.num(d.slot_index, 0) : "--";
      if (d.sol_price_usd != null && !solSeries) {
        const p = fmt.usd(d.sol_price_usd);
        if ($("sol-price")) $("sol-price").textContent = p;
        if ($("sol-mc-price")) $("sol-mc-price").textContent = p;
      }
      const rpcHost = d.rpc ? String(d.rpc).replace(/^https?:\/\//, "").slice(0, 32) : "--";
      const netMeta = $("sol-net-meta");
      if (netMeta) {
        const bits = [];
        bits.push(`<span>rpc <b>${rpcHost}</b></span>`);
        if (d.slots_in_epoch != null) bits.push(`<span>epoch len <b>${fmt.num(d.slots_in_epoch, 0)}</b></span>`);
        if (d.absolute_slot != null) bits.push(`<span>abs <b>${fmt.num(d.absolute_slot, 0)}</b></span>`);
        if (!d.ok && d.error) bits.push(`<span>err <b>${String(d.error).slice(0, 24)}</b></span>`);
        netMeta.innerHTML = bits.join("");
      }
      if (badge) {
        badge.textContent = d.ok ? "live" : "rpc down";
        badge.className = "sol-pressure-badge " + (d.ok ? "ok" : "err");
      }
      if (empty) {
        empty.hidden = !!d.ok;
        if (!d.ok) empty.textContent = "RPC unreachable — will retry quietly";
      }
      if ($("sol-updated")) $("sol-updated").textContent = sastClock();
      pushSolAct(d.ok
        ? `RPC ok · slot ${slotTxt} · epoch ${d.epoch != null ? d.epoch : "--"}`
        : `RPC fail · ${d.error || "timeout"}`);
    } catch (err) {
      if (badge) {
        badge.textContent = "rpc down";
        badge.className = "sol-pressure-badge err";
      }
      if (empty) {
        empty.hidden = false;
        empty.textContent = "RPC unreachable — will retry quietly";
      }
    }
  };

  const ensureSolWorkspace = () => {
    if (!solWorkspaceReady) {
      solWorkspaceReady = true;
      bindSolTf();
      bindSolFilters();
      if (window.LightweightCharts) {
        initSolCandles();
        setSolInterval(solInterval);
        loadSolChg24h();
      } else {
        const el = $("sol-candles");
        const msg = el?.querySelector(".lwc-msg");
        if (msg) msg.textContent = "chart lib unavailable";
      }
      if (window.__lastState) renderSol(window.__lastState);
    }
    requestAnimationFrame(() => {
      resizeSolChart();
      if (solChart) solChart.timeScale().fitContent();
      try { solChartTx?.resize?.(); } catch (_) {}
      try { solChartComp?.resize?.(); } catch (_) {}
      try { solChartArb?.resize?.(); } catch (_) {}
    });
    if (!solRefreshTimer) {
      solRefreshTimer = setInterval(() => {
        if (activeTab !== "sol") return;
        if (solSeries) {
          loadSolKlines(solInterval);
          loadSolChg24h();
        }
      }, 30000);
    }
  };

  const setChainTab = (tab) => {
    activeTab = tab === "sol" ? "sol" : "eth";
    try { localStorage.setItem(TAB_KEY, activeTab); } catch (e) { /* ignore */ }

    const ethPanel = $("tab-eth");
    const solPanel = $("tab-sol");
    const ethBtn = $("tab-btn-eth");
    const solBtn = $("tab-btn-sol");
    const hint = $("chain-tabs-hint");
    const chainLabel = $("p-chain-label");

    if (ethPanel) {
      ethPanel.classList.toggle("active", activeTab === "eth");
      ethPanel.hidden = activeTab !== "eth";
    }
    if (solPanel) {
      solPanel.classList.toggle("active", activeTab === "sol");
      solPanel.hidden = activeTab !== "sol";
    }
    if (ethBtn) {
      ethBtn.classList.toggle("on", activeTab === "eth");
      ethBtn.setAttribute("aria-selected", activeTab === "eth" ? "true" : "false");
    }
    if (solBtn) {
      solBtn.classList.toggle("on", activeTab === "sol");
      solBtn.setAttribute("aria-selected", activeTab === "sol" ? "true" : "false");
    }
    if (hint) {
      hint.textContent = activeTab === "sol"
        ? "Solana workspace · Solend liq + Jupiter arb"
        : "Ethereum workspace · Aave V4 + MEV";
    }
    if (chainLabel) {
      chainLabel.textContent = activeTab === "sol" ? "SOL mainnet" : "ETH mainnet";
    }

    if (activeTab === "sol") {
      ensureSolWorkspace();
      if (window.__lastState) renderSol(window.__lastState);
    } else {
      if (window.__lastState) {
        const s = window.__lastState;
        updateHeader(s);
        updateBots(s); updateFunds(s); updateMempool(s);
        updateOpps(s); updateCompetitors(s); updateArb(s); updateIntel(s); updateBroadcast(s); updatePrices(s);
      }
      requestAnimationFrame(() => {
        resizeEthChart();
        try { chartTx?.resize?.(); } catch (_) {}
        try { chartComp?.resize?.(); } catch (_) {}
        try { chartArb?.resize?.(); } catch (_) {}
      });
    }
  };

  const bindChainTabs = () => {
    const nav = $("chain-tabs");
    if (nav && !nav.__bound) {
      nav.__bound = true;
      nav.addEventListener("click", (ev) => {
        const btn = ev.target.closest(".chain-tab[data-tab]");
        if (!btn) return;
        setChainTab(btn.getAttribute("data-tab"));
      });
    }
    let saved = "eth";
    try { saved = localStorage.getItem(TAB_KEY) || "eth"; } catch (e) { saved = "eth"; }
    setChainTab(saved === "sol" ? "sol" : "eth");
  };

  /* ------------------------------------------------ websocket */
  let ws = null;
  const connect = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => { $("sys-info").textContent = "ws connected"; };
    ws.onclose = () => { setTimeout(connect, 3000); };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "state") render(msg.data);
        else if (msg.type === "log") {
          appendLogLine(msg.line);
        }
      } catch (e) { console.error(e); }
    };
  };
  const render = (s) => {
    window.__lastBcast = s.broadcast || {};
    window.__lastState = s;
    updateHeader(s);
    if (activeTab === "sol") {
      renderSol(s);
      return;
    }
    /* Heavy ETH card DOM only while ETH tab is visible — WS stays connected */
    updateBots(s); updateFunds(s); updateMempool(s);
    updateOpps(s); updateCompetitors(s); updateArb(s); updateIntel(s); updateBroadcast(s); updatePrices(s);
    if (!window.__logInit) { updateLog(s, s.log); window.__logInit = true; }
  };

  /* ------------------------------------------------ web3 / wallet */
  let provider = null;
  let signerAddr = null;
  const btnConnect = $("btn-connect");
  const chip = $("wallet-chip");

  btnConnect.addEventListener("click", async () => {
    if (!window.ethereum) {
      btnConnect.textContent = "No MetaMask";
      return;
    }
    try {
      provider = new ethers.BrowserProvider(window.ethereum);
      await provider.send("eth_requestAccounts", []);
      const signer = await provider.getSigner();
      signerAddr = await signer.getAddress();
      const net = await provider.getNetwork();
      const bal = await provider.getBalance(signerAddr);
      chip.textContent = `${signerAddr.slice(0, 6)}…${signerAddr.slice(-4)} | chain ${net.chainId} | ${fmt.eth(Number(bal) / 1e18, 3)}`;
      chip.classList.remove("hidden");
      btnConnect.textContent = "Connected";
      btnConnect.disabled = true;
    } catch (e) {
      btnConnect.textContent = "Connect error";
      console.error(e);
    }
  });

  $("btn-fund").addEventListener("click", async () => {
    const amt = parseFloat($("fund-amt").value);
    const st = $("fund-status");
    if (!provider || !signerAddr) { st.textContent = "connect a wallet first"; return; }
    if (!amt || amt <= 0) { st.textContent = "enter an amount"; return; }
    try {
      st.textContent = "requesting signature...";
      const signer = await provider.getSigner();
      const tx = await signer.sendTransaction({ to: SPONSOR, value: ethers.parseEther(String(amt)) });
      st.textContent = `tx ${tx.hash.slice(0, 10)}… sent, waiting confirm`;
      await tx.wait();
      st.textContent = `confirmed: ${fmt.eth(amt)} to sponsor`;
      $("fund-amt").value = "0.07";
    } catch (e) {
      st.textContent = "cancelled / " + String(e.message || e).slice(0, 60);
    }
  });

  bindAlControls();
  setInterval(() => { if (logLines.length) updateAlHero(); }, 5000);
  bindMcTf();
  if (window.LightweightCharts) {
    initCandles();
    setMcInterval(mcInterval);
    loadEthChg24h();
    setInterval(() => {
      if (activeTab !== "eth") return;
      loadKlines(mcInterval);
      loadEthChg24h();
    }, 15000);
  } else {
    const el = $("eth-candles");
    if (el) {
      const msg = el.querySelector(".lwc-msg");
      if (msg) msg.textContent = "chart lib unavailable";
    }
  }
  bindChainTabs();

  connect();
  tickSast();
  setInterval(tickSast, 1000);

  const btnSim = $("btn-sim");
  const btnArm = $("btn-arm");
  const btnEdge = $("btn-edge");
  if (btnSim) btnSim.addEventListener("click", async () => {
    const cur = window.__lastBcast || {};
    await postControl({sim_only: !cur.sim_only});
  });
  if (btnArm) btnArm.addEventListener("click", async () => {
    const cur = window.__lastBcast || {};
    if (cur.armed) await postControl({armed: false});
    else await postControl({armed: true, sim_only: false, arm_minutes: 15});
  });
  if (btnEdge) btnEdge.addEventListener("click", async () => {
    const cur = window.__lastBcast || {};
    await postControl({edge_bias: !cur.edge_bias});
  });

  const solBtnSim = $("sol-btn-sim");
  const solBtnArm = $("sol-btn-arm");
  const solBtnEdge = $("sol-btn-edge");
  if (solBtnSim) solBtnSim.addEventListener("click", async () => {
    const cur = window.__lastSolBcast || {};
    await postSolControl({sim_only: !cur.sim_only});
  });
  if (solBtnArm) solBtnArm.addEventListener("click", async () => {
    const cur = window.__lastSolBcast || {};
    if (cur.armed) await postSolControl({armed: false});
    else await postSolControl({armed: true, sim_only: false, arm_minutes: 15});
  });
  if (solBtnEdge) solBtnEdge.addEventListener("click", async () => {
    const cur = window.__lastSolBcast || {};
    await postSolControl({edge_bias: !cur.edge_bias});
  });
})();
