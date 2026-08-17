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
  let solOpLastMeta = {}, solCpLastMeta = {};
  const solHfUrgency = (hf) => {
    if (hf == null || hf >= 100) return { cls: "ok", label: "—" };
    if (hf < 1.0) return { cls: "crit", label: "liq" };
    if (hf < 1.05) return { cls: "hot", label: "hot" };
    if (hf < 1.1) return { cls: "warm", label: "warm" };
    if (hf < 1.25) return { cls: "ok", label: "ok" };
    return { cls: "ok", label: "safe" };
  };
  const solHfClass = (hf) => {
    if (hf == null) return "op-hf-ok";
    if (hf < 1.0) return "op-hf-crit";
    if (hf < 1.05) return "op-hf-hot";
    if (hf < 1.1) return "op-hf-warm";
    return "op-hf-ok";
  };
  const solShortPk = (pk) => {
    const s = pk || "";
    if (!s) return "--";
    return s.length > 12 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
  };
  const solLiqFlagBits = (o) => {
    const bits = [];
    if (o.actionable && o.submit === "live") bits.push(`<span class="pill ok">LIVE</span>`);
    else if (o.actionable) bits.push(`<span class="pill warn">+EV sim</span>`);
    if (o.submit === "blocked")
      bits.push(`<span class="pill blocked" title="${o.submit_reason || "blocked"}">blocked</span>`);
    else if (o.submit === "sim")
      bits.push(`<span class="pill" title="${o.submit_reason || "sim-only"}">sim</span>`);
    if (o.flash || o.use_flash)
      bits.push(`<span class="pill accent" title="Solend flash ${o.flash_fee_bps != null ? o.flash_fee_bps + " bps" : ""}">flash</span>`);
    if (o.flash_fee_bps != null && Number(o.flash_fee_bps) > 0)
      bits.push(`<span class="pill" title="flash fee ${fmt.usd(o.flash_fee_usd)}">${fmt.num(o.flash_fee_bps, 0)} bps</span>`);
    const left = [].concat(o.leftover || o.account_gaps || (o.plan && o.plan.account_gaps) || []).filter(Boolean);
    if (left.length)
      bits.push(`<span class="pill blocked" title="${left.join(", ")}">leftover</span>`);
    if (o.race || o.contested)
      bits.push(`<span class="pill warn">race</span>`);
    if (o.edge) bits.push(`<span class="pill accent">edge</span>`);
    if (o.source) bits.push(`<span class="pill">${o.source}</span>`);
    const user = o.obligation || o.user || "";
    if (user) {
      bits.push(`<a class="op-link" href="https://solscan.io/account/${user}" target="_blank" rel="noopener">↗</a>`);
      bits.push(`<span class="mono copy op-link" data-addr="${user}" title="${user}">copy</span>`);
    }
    return bits.join(" ") || `<span class="dim">—</span>`;
  };
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
    const funds = sol.funds || {};
    renderBotsFleet({
      listId: "sol-bots-list",
      tagId: "sol-bots-tag",
      pressureId: "sol-bots-pressure",
      modeId: "sol-bots-mode",
      walletsId: "sol-bots-wallets",
      counts: { ok: "sol-bots-n-ok", run: "sol-bots-n-run", err: "sol-bots-n-err", idle: "sol-bots-n-idle" },
      bots: (sol || {}).bots,
      labels: {
        mempool: "Priority Fee Watcher", prices: "Slot / SOL Price", funds: "SOL Funds",
        sweep: "Solend Opportunity Sweep", competitors: "Solend Program Watch",
        arb: "Jupiter Arb Scanner", intel: "SOL Learning / Intel", broadcast: "SOL Broadcast",
      },
      roles: {
        mempool: "landing", prices: "oracle", funds: "wallets", sweep: "HF",
        competitors: "liq", arb: "Jup", intel: "learn", broadcast: "submit",
      },
      funds,
      wallets: (sol || {}).wallets || {
        funder: (funds.funder || {}).pubkey,
        sponsor: (funds.sponsor || {}).pubkey,
        bot: (funds.bot || {}).pubkey,
      },
      bc: (sol || {}).broadcast,
      unit: "SOL",
      balKey: "sol",
    });
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
    const g = sol.fund_guide || {};
    const sp = (funds.sponsor || {}).pubkey || g.sponsor || wallets.sponsor || "";
    const bt = (funds.bot || {}).pubkey || g.bot || wallets.bot || "";
    const fd = (funds.funder || {}).pubkey || g.from_pubkey || wallets.funder || "";
    const ts = g.sponsor_target_sol != null ? g.sponsor_target_sol : 0.08;
    const tb = g.bot_target_sol != null ? g.bot_target_sol : 0.25;
    const check = $("sol-fund-checklist");
    if (check) {
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
    const amtEl = $("sol-fund-amt");
    if (amtEl && amtEl.dataset.dirty !== "1") {
      const tgt = Number(ts);
      if (tgt > 0) amtEl.value = String(tgt);
    }
    const hint = $("sol-fund-hint");
    if (hint) {
      const sf = Number((funds.sponsor || {}).shortfall_sol || 0);
      hint.textContent = sf > 0
        ? `sponsor shortfall ${fmt.num(sf, 4)} SOL`
        : (sp ? `sponsor ${sp.slice(0, 4)}…${sp.slice(-4)}` : "sponsor pubkey unset");
      hint.className = sf > 0 ? "amber" : "dim";
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
    const kindOf = (t) => (t.kind || t.flags || t.cls || "").toLowerCase();
    const live = solMpFilter === "all" ? solMpLiveCache
      : solMpLiveCache.filter((t) => {
          const k = kindOf(t);
          if (solMpFilter === "mev") return k === "jito" || k === "backrun" || k === "mev";
          if (solMpFilter === "hot") return t.cls === "hot" || k === "liq";
          return k === solMpFilter || t.cls === solMpFilter;
        });
    if (note) note.textContent = `${live.length}/${solMpLiveCache.length} · landing`;
    if (empty) empty.style.display = live.length ? "none" : "block";
    body.innerHTML = live.slice(0, 50).map((t) => {
      const slot = t.slot != null ? String(t.slot) : "--";
      const kind = t.kind || t.flags || t.cls || "?";
                    const link = t.solscan
        ? `<a href="${t.solscan}" target="_blank" rel="noopener" style="color:var(--violet)">${(t.sig || t.tx || slot).toString().slice(0, 10)}</a>` +
          (t.sig || t.tx ? ` <span class="mono copy op-link" data-addr="${t.sig || t.tx}" title="${t.sig || t.tx}">copy</span>` : "")
        : `<span class="mono dim">${slot.slice(-8)}</span>`;
      const pair = t.pair || t.searcher || "";
      const hf = t.hf != null ? fmt.num(t.hf, 3) : (t.fee != null ? fmtUl(t.fee) : "—");
      const net = t.profit_usd != null ? fmt.usd(t.profit_usd, 3)
        : (t.vs_med != null ? fmt.num(t.vs_med, 1) + "×" : "—");
      return `<tr>
        <td><span class="sol-kind ${kind}">${kind}</span></td>
        <td class="mono" title="${slot}">${slot}</td>
        <td>${pair}</td>
        <td>${hf}</td>
        <td class="${Number(t.profit_usd) > 0 ? "green" : "dim"}">${net}</td>
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
    set("sol-mp-count", fmt.num(meta.liq_hits != null ? meta.liq_hits : 0));
    set("sol-mp-queued", fmt.num(meta.mev_hits != null ? meta.mev_hits : (meta.jito_bundles || 0)), "dim");
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
      metaEl.innerHTML =
        `<span>p99 <b>${fmtUl(meta.p99_fee)}</b></span>` +
        `<span>decoded <b>${fmt.num(meta.decoded, 0)}</b></span>` +
        `<span>refresh <b>${fmt.num(meta.refresh_n, 0)}</b></span>` +
        `<span>jito <b>${fmt.num(meta.jito_bundles, 0)}</b></span>` +
        `<span>race <b>${fmt.num(meta.contested, 0)}</b></span>` +
        `<span>TPS <b>${tps}</b></span>` +
        `<span class="dim">${meta.landing_note || "landing + µl/CU"}</span>`;
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
    solMpLiveCache = (m.hits && m.hits.length) ? m.hits : (m.mev_txs || []);
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
    else if (solOpFilter === "profit") rows = rows.filter((o) => Number(o.net_usd != null ? o.net_usd : o.profit_usd) > 0);
    else if (solOpFilter === "race") rows = rows.filter((o) => o.race || o.contested);
    else if (solOpFilter === "hf1") rows = rows.filter((o) => o.hf != null && o.hf < 1);
    const note = $("sol-op-feed-note");
    if (note) note.textContent = `${rows.length}/${solOpCache.length} · HF<1 · skip dust`;
    if (empty) {
      if (rows.length) {
        empty.style.display = "none";
        empty.classList.remove("err");
      } else {
        empty.style.display = "block";
        const m = solOpLastMeta || {};
        if (m.status && String(m.status).startsWith("err")) {
          empty.classList.add("err");
          empty.textContent = "sweep error: " + m.status;
        } else if (m.last_scan || m.last_slot) {
          empty.classList.remove("err");
          empty.textContent = `no HF<1 +EV this scan · ${fmt.num(m.scanned || m.obligation_hydrated || 0, 0)} hydrates`
            + (m.obligation_probed != null ? ` · ${fmt.num(m.obligation_probed, 0)} GPA` : "")
            + (m.last_slot ? ` · slot ${m.last_slot}` : "")
            + (m.last_scan ? ` · ${fmt.age(m.last_scan)} ago` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "scanning Solend obligations for HF<1…";
        }
      }
    }
    body.innerHTML = rows.slice(0, 60).map((o) => {
      const user = o.obligation || o.user || "";
      const short = solShortPk(user);
      const hf = o.hf;
      const hfCell = hf == null ? "--" : (hf >= 100 ? "∞" : Number(hf).toFixed(3));
      const pair = `${o.coll_sym || o.collateral_sym || "?"} → ${o.debt_sym || "?"}`;
      const sizes = (o.coll_usd != null || o.debt_usd != null)
        ? `<div class="dim">${fmt.usd(o.coll_usd)} / ${fmt.usd(o.debt_usd)}</div>` : "";
      const net = o.net_usd != null ? o.net_usd : o.profit_usd;
      const netColor = net == null ? "var(--dim)" : net > 0 ? "var(--green)" : "var(--red)";
      return `<tr>
        <td class="mono copy" data-addr="${user}" title="${user}">${short}</td>
        <td class="${solHfClass(hf)}">${hfCell}</td>
        <td><b>${pair}</b>${sizes}</td>
        <td style="color:var(--amber)">${o.liq_bonus_pct != null ? o.liq_bonus_pct + "%" : (o.bonus_usd != null ? fmt.usd(o.bonus_usd) : "--")}</td>
        <td style="color:${netColor}"><b>${net != null ? fmt.usd(net) : "--"}</b></td>
        <td>${solLiqFlagBits(o)}</td>
      </tr>`;
    }).join("");
  };

  const updateSolOpps = (sol) => {
    const meta = sol.opportunities_meta || {};
    solOpLastMeta = meta;
    const opps = (sol.opportunities || []).filter((o) => !o.proxy && o.hf != null && o.hf < 1);
    const wl = (sol.watchlist || []).filter((w) => !w.proxy && w.hf != null);
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    const count = meta.count != null ? meta.count : opps.length;
    set("sol-op-count", fmt.num(count, 0));
    set("sol-op-best", meta.best_profit ? fmt.usd(meta.best_profit) : "--", "green");
    set("sol-op-edge-n", fmt.num(meta.edge_n, 0), "amber");
    set("sol-op-sweep", fmt.num(meta.scanned != null ? meta.scanned : (meta.obligation_hydrated || 0), 0), "dim");
    const badge = $("sol-op-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "op-pressure-badge " + (meta.pressure || "idle");
    }
    const closest = wl[0];
    const closestEl = $("sol-op-closest-hf");
    const closestHf = closest && closest.hf != null ? Number(closest.hf) : null;
    if (closestEl) {
      closestEl.textContent = closestHf == null ? "--" : (closestHf >= 100 ? "∞" : closestHf.toFixed(4));
      closestEl.className = "big " + (closestHf != null && closestHf < 1.05 ? "red" : closestHf != null && closestHf < 1.1 ? "amber" : "dim");
    }
    const cu = $("sol-op-closest-user");
    if (cu) {
      const pk = closest ? (closest.user || closest.obligation || "") : "";
      cu.textContent = pk ? solShortPk(pk) : "--";
      cu.className = "dim mono copy";
      if (pk) { cu.dataset.addr = pk; cu.title = pk; }
    }
    const urg = $("sol-op-urgency");
    if (urg) {
      const buckets = [
        { label: "<1.00", n: wl.filter((w) => Number(w.hf) < 1).length },
        { label: "1–1.05", n: wl.filter((w) => { const h = Number(w.hf); return h >= 1 && h < 1.05; }).length },
        { label: "1.05–1.1", n: wl.filter((w) => { const h = Number(w.hf); return h >= 1.05 && h < 1.1; }).length },
        { label: "1.1+", n: wl.filter((w) => Number(w.hf) >= 1.1).length },
      ];
      const maxN = Math.max(1, ...buckets.map((b) => b.n));
      urg.innerHTML = buckets.map((b) =>
        `<div class="op-urg-row"><span>${b.label}</span>` +
        `<div class="op-urg-bar"><i style="width:${Math.round(100 * b.n / maxN)}%"></i></div>` +
        `<span>${b.n}</span></div>`).join("");
    }
    const metaEl = $("sol-op-meta");
    if (metaEl) {
      const sweepBot = (sol.bots || {}).sweep || {};
      const gate = meta.submit_gate || "blocked";
      metaEl.innerHTML =
        `<span>Σ net <b style="color:var(--green)">${fmt.usd(meta.sum_profit)}</b></span>` +
        `<span>watch <b>${fmt.num(wl.length, 0)}</b></span>` +
        (meta.scanned != null ? `<span>scanned <b>${fmt.num(meta.scanned, 0)}</b></span>` : "") +
        (meta.obligation_hydrated != null ? `<span>hyd <b>${fmt.num(meta.obligation_hydrated, 0)}</b></span>` : "") +
        (sweepBot.status ? `<span>sweep <b>${sweepBot.status}</b></span>` : "") +
        `<span>gate <b>${gate}</b></span>` +
        (meta.last_slot ? `<span>slot <b>${meta.last_slot}</b></span>` : "") +
        (meta.last_scan ? `<span>${fmt.age(meta.last_scan)} ago</span>` : "") +
        (meta.avg_hf != null ? `<span>avg HF <b>${Number(meta.avg_hf).toFixed(3)}</b></span>` : "");
    }
    const mix = (meta.pair_mix && meta.pair_mix.length) ? meta.pair_mix : [];
    const track = $("sol-op-mix-track");
    const keys = $("sol-op-mix-keys");
    const tot = mix.reduce((a, m) => a + (m.n || 0), 0) || 1;
    if (track) {
      track.innerHTML = mix.length
        ? mix.map((m, i) =>
            `<span style="width:${Math.max(4, m.pct || (100 * (m.n || 0) / tot))}%;background:${palette[i % palette.length]}" title="${m.pair}"></span>`
          ).join("")
        : `<span style="width:100%;background:#334155"></span>`;
    }
    if (keys) {
      keys.innerHTML = mix.length
        ? mix.map((m) => `<span>${m.pair} <b>${m.n}</b></span>`).join("")
        : `<span class="dim">no liquidatable pairs</span>`;
    }
    solOpCache = opps.slice(0, 80);
    renderSolOpps();
    const wnote = $("sol-watch-note");
    if (wnote) wnote.textContent = `${Math.min(wl.length, 10)} tracked · lowest HF`;
    const wbody = $("sol-watch-table") && $("sol-watch-table").querySelector("tbody");
    if (wbody) {
      wbody.innerHTML = wl.slice(0, 10).map((w) => {
        const hf = Number(w.hf);
        const hfCell = hf >= 100 ? "∞" : hf.toFixed(3);
        const urg = solHfUrgency(hf);
        const user = w.user || w.obligation || "";
        return `<tr>
          <td class="mono copy" data-addr="${user}" title="click to copy">${solShortPk(user)}</td>
          <td class="${solHfClass(hf)}">${hfCell}</td>
          <td>${fmt.usd(w.coll_usd)}</td>
          <td>${fmt.usd(w.debt_usd)}</td>
          <td><span class="op-urg ${urg.cls}">${urg.label}</span></td>
        </tr>`;
      }).join("") || `<tr><td colspan="5" class="dim">waiting for Solend obligation hydrates…</td></tr>`;
    }
  };

  const renderSolComps = () => {
    const body = $("sol-comp-table") && $("sol-comp-table").querySelector("tbody");
    const empty = $("sol-comp-empty");
    if (!body) return;
    let rows = solCpCache;
    if (solCpFilter === "miss") rows = rows.filter((r) => r.missed || r.missed_by_us);
    else if (solCpFilter === "edge") rows = rows.filter((r) => r.edge);
    else if (solCpFilter === "profit") rows = rows.filter((r) => Number(r.net != null ? r.net : r.est) > 0);
    else if (solCpFilter === "revert") rows = rows.filter((r) => /revert/i.test(r.flags || ""));
    const note = $("sol-cp-feed-note");
    if (note) note.textContent = `${rows.length}/${solCpCache.length} · newest first`;
    if (empty) {
      if (rows.length) {
        empty.style.display = "none";
        empty.classList.remove("err");
      } else {
        empty.style.display = "block";
        const m = solCpLastMeta || {};
        if (m.status && String(m.status).startsWith("err")) {
          empty.classList.add("err");
          empty.textContent = "scan error: " + m.status;
        } else if (m.last_scan || m.last_slot) {
          empty.classList.remove("err");
          empty.textContent = `no Solend liquidations this window`
            + (m.scanned != null ? ` · ${fmt.num(m.scanned, 0)} sigs` : "")
            + (m.last_slot ? ` · slot ${m.last_slot}` : "")
            + (m.last_scan ? ` · ${fmt.age(m.last_scan)} ago` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "scanning Solend liquidate signatures…";
        }
      }
    }
    body.innerHTML = rows.slice(0, 50).map((c) => {
      const searcher = c.searcher || "";
      const user = c.user || "";
      const sig = c.sig || c.tx || "";
      const net = c.net != null ? c.net : c.net_est_usd;
      const netColor = net == null ? "var(--dim)" : net >= 0 ? "var(--green)" : "var(--red)";
      const flags = [];
      if (c.missed || c.missed_by_us) flags.push(`<span class="cp-flag miss">miss</span>`);
      if (c.edge) flags.push(`<span class="cp-flag edge">edge</span>`);
      if (/revert/i.test(c.flags || "")) flags.push(`<span class="cp-flag revert">revert</span>`);
      flags.push(`<span class="pill">solend</span>`);
      const tx = sig
        ? `<a href="${c.solscan || ("https://solscan.io/tx/" + sig)}" target="_blank" rel="noopener" style="color:var(--cyan)">${sig.slice(0, 8)}…</a>` +
          ` <span class="mono copy op-link" data-addr="${sig}" title="${sig}">copy</span>`
        : `<span class="dim">--</span>`;
      return `<tr>
        <td class="dim" title="slot ${c.slot || "?"}">${c.ts ? fmt.age(c.ts) : (c.slot || "--")}</td>
        <td><b>${c.pair || "solend-liq"}</b></td>
        <td class="mono copy" data-addr="${searcher}" title="${searcher}">${solShortPk(searcher)}</td>
        <td class="mono copy dim" data-addr="${user}" title="${user}">${solShortPk(user)}</td>
        <td>${c.gas_usd != null ? fmt.usd(c.gas_usd) : "--"}</td>
        <td style="color:${c.est != null ? "var(--amber)" : "var(--dim)"}">${c.est != null ? fmt.usd(c.est) : "n/a"}</td>
        <td style="color:${netColor}"><b>${net != null ? fmt.usd(net) : "--"}</b></td>
        <td>${flags.join(" ")}</td>
        <td>${tx}</td>
      </tr>`;
    }).join("");
  };

  const updateSolCompetitors = (sol, hist) => {
    const meta = sol.competitors_meta || {};
    solCpLastMeta = meta;
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    set("sol-cp-count", fmt.num(meta.count_1h, 0));
    set("sol-cp-searchers", fmt.num(meta.unique_searchers, 0), "dim");
    set("sol-cp-sum-est", meta.sum_est_profit ? fmt.usd(meta.sum_est_profit) : "—", "amber");
    set("sol-cp-missed",
      meta.missed_by_us
        ? `${fmt.num(meta.missed_by_us, 0)}${meta.miss_rate_pct ? ` · ${fmt.num(meta.miss_rate_pct, 0)}%` : ""}`
        : "0", "red");
    const badge = $("sol-cp-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "cp-pressure-badge " + (meta.pressure || "idle");
    }
    const metaEl = $("sol-cp-meta");
    if (metaEl) {
      metaEl.innerHTML =
        `<span>Σ miss <b>${fmt.num(meta.missed_by_us, 0)}</b></span>` +
        `<span>edge <b>${fmt.num(meta.edge_n, 0)}</b></span>` +
        `<span>reverts <b>${fmt.num(meta.revert_n, 0)}</b></span>` +
        `<span>scanned <b>${fmt.num(meta.scanned, 0)}</b></span>` +
        `<span>tracked <b>${fmt.num(meta.total, 0)}</b></span>` +
        (meta.last_slot ? `<span>slot <b>${meta.last_slot}</b></span>` : "") +
        (meta.last_scan ? `<span>${fmt.age(meta.last_scan)} ago</span>` : "") +
        (meta.status && meta.status !== "ok" ? `<span style="color:var(--red)">${meta.status}</span>` : "");
    }
    solCpCache = sol.competitors || [];
    renderSolComps();
    const sbody = $("sol-cp-searcher-table") && $("sol-cp-searcher-table").querySelector("tbody");
    const sempty = $("sol-cp-searcher-empty");
    const tops = meta.top_searchers || [];
    if (sempty) sempty.style.display = tops.length ? "none" : "block";
    if (sbody) {
      sbody.innerHTML = tops.map((t, i) =>
        `<tr><td>${i + 1}</td>
          <td class="mono copy" data-addr="${t.searcher || ""}" title="${t.searcher || ""}">${solShortPk(t.searcher)}</td>
          <td>${t.share != null ? Math.round((t.share || 0) * 100) + "%" : ""}</td>
          <td>${t.n}</td><td>${t.sum_est ? fmt.usd(t.sum_est) : "—"}</td></tr>`
      ).join("");
    }
    const pbody = $("sol-cp-pair-table") && $("sol-cp-pair-table").querySelector("tbody");
    const pnote = $("sol-cp-pair-note");
    const mix = meta.pair_mix || [];
    if (pnote) pnote.textContent = String(mix.length);
    if (pbody) {
      pbody.innerHTML = mix.map((p) =>
        `<tr><td>${p.pair}</td>
          <td><div class="cp-bar-track"><div class="cp-bar pair" style="width:${Math.min(100, p.pct || 0)}%"></div></div></td>
          <td>${p.n}</td><td class="dim">${p.pct ?? ""}%</td></tr>`
      ).join("");
    }
    const track = $("sol-cp-mix-track");
    const keys = $("sol-cp-mix-keys");
    const tot = mix.reduce((a, m) => a + (m.n || 0), 0) || 1;
    if (track) {
      track.innerHTML = mix.length
        ? mix.map((m, i) =>
            `<span style="width:${Math.max(4, m.pct || (100 * (m.n || 0) / tot))}%;background:${palette[i % palette.length]}"></span>`
          ).join("")
        : `<span style="width:100%;background:#334155"></span>`;
    }
    if (keys) keys.innerHTML = mix.map((m) => `<span>${m.pair} <b>${m.n}</b></span>`).join("")
      || `<span class="dim">no liquidations this window</span>`;
    const ch = (hist && hist.sol_comp_1h) || [];
    if (solChartComp && ch.length) {
      solChartComp.data.labels = ch.map((_, i) => i);
      solChartComp.data.datasets[0].data = ch;
      solChartComp.data.datasets[1].data = ch.map(() => 0);
      solChartComp.update("none");
    }
  };

  const SOL_VENUE_COLORS = {
    jup: "#a78bfa", jupiter: "#a78bfa",
    raydium: "#22d3ee", "raydium clmm": "#22d3ee",
    orca: "#f472b6", whirlpool: "#f472b6",
    meteora: "#f59e0b", "meteora dlmm": "#f59e0b",
    phoenix: "#22c55e", lifinity: "#38bdf8",
  };

  const solVenueBadge = (o) => {
    const raw = String(o.venue || o.hop_src?.[0] || o.dex || "jupiter");
    const v = raw.toLowerCase();
    const cls = o.cross_dex || v.includes("→") || v.includes("+") ? "cross"
      : (v.includes("raydium") ? "raydium" : v.includes("orca") || v.includes("whirl") ? "orca"
        : v.includes("meteora") ? "meteora" : v.includes("phoenix") ? "phoenix" : "jupiter");
    const label = (o.cross_dex ? raw : (o.hop_src && o.hop_src[0]) || raw).slice(0, 28);
    return `<span class="ar-venue ${cls}">${label}</span>`;
  };

  const solArbFlag = (o) => {
    const bits = [];
    if (o.actionable && o.submit === "live") bits.push(`<span class="pill ok">LIVE</span>`);
    else if (o.actionable) bits.push(`<span class="pill warn">+EV sim</span>`);
    if (o.submit === "blocked")
      bits.push(`<span class="pill blocked" title="${o.submit_reason || "blocked"}">blocked</span>`);
    else if (o.submit === "sim")
      bits.push(`<span class="pill" title="${o.submit_reason || "sim-only"}">sim</span>`);
    if (o.use_flash || o.flash)
      bits.push(`<span class="pill accent">flash</span>`);
    if (o.flash_fee_bps)
      bits.push(`<span class="pill" title="Solend flash ${fmt.usd(o.flash_fee_usd)}">${fmt.num(o.flash_fee_bps, 0)} bps</span>`);
    const left = [].concat(o.leftover || (o.plan && o.plan.leftover) || []).filter(Boolean);
    if (left.length)
      bits.push(`<span class="pill blocked" title="${left.join(", ")}">leftover</span>`);
    if (o.cross_dex) bits.push(`<span class="pill accent">cross</span>`);
    const src = String(o.quote_src || "").split("+")[0];
    if (src && src !== "jupiter")
      bits.push(`<span class="pill">${src}</span>`);
    bits.push(`<span class="sol-kind mev">${o.kind || "mev"}</span>`);
    return bits.join(" ");
  };

  const renderSolArb = () => {
    const body = $("sol-arb-table") && $("sol-arb-table").querySelector("tbody");
    const empty = $("sol-arb-empty");
    if (!body) return;
    let rows = solArCache.filter((o) => Number(o.net_usd) > 0 && !o.same_pool);
    if (solArFilter === "live") rows = rows.filter((o) => Number(o.net_usd) > 0 && o.actionable);
    else if (solArFilter === "cross") rows = rows.filter((o) => o.cross_dex);
    else if (solArFilter === "local") rows = rows.filter((o) =>
      /raydium-account|orca-account/i.test(o.quote_src || o.source || ""));
    else if (solArFilter === "jup") rows = rows.filter((o) =>
      /jup|jupiter/i.test(o.dex || o.quote_src || o.source || o.venue || ""));
    const note = $("sol-ar-feed-note");
    if (note) note.textContent = rows.length ? `${rows.length} +EV` : "+EV after costs";
    const m = solArMeta || {};
    const a = solArLastState || {};
    if (empty) {
      if (rows.length) {
        empty.style.display = "none";
        empty.classList.remove("err");
      } else {
        empty.style.display = "block";
        if (solArError || a.error) {
          empty.classList.add("err");
          empty.textContent = "scan error: " + (solArError || a.error);
        } else if (m.last_scan || a.last_scan) {
          empty.classList.remove("err");
          const n = m.pairs != null ? m.pairs : (m.pairs_tried || 0);
          const q = m.quoted != null ? m.quoted : m.quotes;
          const skip = m.skipped != null ? m.skipped : (a.near || []).length;
          const floor = m.min_usd;
          const mix = m.quote_src_mix || {};
          const mixBits = Object.keys(mix).length
            ? Object.entries(mix).map(([k, v]) => `${k}:${v}`).join(" ")
            : (m.quote_src || "");
          const loc = m.local_n != null ? m.local_n : 0;
          const jn = m.jup_n != null ? m.jup_n : 0;
          empty.textContent = `no +EV this scan · ${fmt.num(n, 0)} pairs`
            + (q != null ? ` · ${fmt.num(q, 0)} quotes` : "")
            + (skip != null ? ` · skip ${fmt.num(skip, 0)}` : "")
            + (floor != null ? ` · floor ${fmt.usd(floor, 4)}` : "")
            + ` · local ${fmt.num(loc, 0)} / jup ${fmt.num(jn, 0)}`
            + (m.pools_decoded != null ? ` · pools ${fmt.num(m.pools_decoded, 0)}` : "")
            + (mixBits ? ` · ${mixBits}` : "")
            + (m.last_scan ? ` · last ${fmt.age(m.last_scan)}` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "waiting for first pool-account scan…";
        }
      }
    }
    body.innerHTML = rows.slice(0, 40).map((o) => {
      const netColor = o.net_usd > 0 ? "var(--green)" : "var(--dim)";
      const hopTip = (o.hop_src || o.labels || []).join(" → ");
      return `<tr>
        <td>${solVenueBadge(o)}</td>
        <td><b>${o.mid || o.path || "?"}</b></td>
        <td class="mono dim" title="${hopTip}">${o.route || hopTip || "--"}</td>
        <td>${fmt.num(o.hops || 2, 0)}</td>
        <td>${o.borrow || ""}</td>
        <td style="color:var(--amber)">${fmt.usd(o.gross_usd, 4)}</td>
        <td class="dim">${fmt.usd(o.gas_usd, 4)}</td>
        <td style="color:${netColor}"><b>${fmt.usd(o.net_usd, 4)}</b></td>
        <td>${solArbFlag(o)}</td>
      </tr>`;
    }).join("");
  };

  let solArMeta = {};
  let solArError = null;
  let solArLastState = {};

  const updateSolArb = (sol, hist) => {
    const a = sol.arb || sol.mev || {};
    const meta = a.meta || {};
    solArMeta = meta;
    solArError = a.error || null;
    solArLastState = a;
    const set = (id, v) => { const e = $(id); if (e) e.textContent = v; };
    set("sol-ar-live", fmt.num(meta.live, 0));
    set("sol-ar-actionable", fmt.num(meta.actionable, 0));
    set("sol-ar-best-net", meta.best_net_usd != null && meta.live ? fmt.usd(meta.best_net_usd, 4) : "--");
    set("sol-ar-skip-n", fmt.num(meta.skipped != null ? meta.skipped : (meta.near || 0), 0));
    set("sol-ar-skip-debug-n", fmt.num(meta.skipped != null ? meta.skipped : (a.near || []).length, 0));
    const badge = $("sol-ar-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "ar-pressure-badge " + (meta.pressure || "idle");
    }
    const metaEl = $("sol-arb-meta");
    if (metaEl) {
      const uni = a.universe && !Array.isArray(a.universe) ? a.universe : {};
      const dexes = (meta.dexes || uni.venues || ["raydium", "orca"]).join("+") || "local";
      const gate = meta.submit_gate || "blocked";
      const gateColor = gate === "live" ? "var(--green)" : gate === "sim" ? "var(--amber)" : "var(--red)";
      const age = meta.last_scan ? fmt.age(meta.last_scan) : (a.last_scan ? fmt.age(a.last_scan) : null);
      const sample = meta.sample_route || {};
      const hopLabs = (sample.labels || []).join("→");
      const qmix = meta.quote_src_mix || {};
      const mixBits = Object.keys(qmix).length
        ? Object.entries(qmix).map(([k, v]) => `${k}:${v}`).join(" ")
        : (meta.quote_src || "—");
      metaEl.innerHTML =
        `<span>pairs <b>${fmt.num(meta.pairs != null ? meta.pairs : uni.pairs, 0)}</b></span>` +
        `<span>dexes <b>${dexes}</b></span>` +
        `<span>submit <b style="color:${gateColor}">${gate}</b></span>` +
        `<span>cross <b style="color:var(--violet)">${fmt.num(meta.cross_dex, 0)}</b></span>` +
        `<span>quote_src <b>${mixBits}</b></span>` +
        `<span>local <b>${fmt.num(meta.local_n, 0)}</b></span>` +
        `<span>jup <b>${fmt.num(meta.jup_n, 0)}</b></span>` +
        (meta.pools_decoded != null ? `<span>pools <b>${fmt.num(meta.pools_decoded, 0)}/${fmt.num(meta.pools_watch != null ? meta.pools_watch : 11, 0)}</b></span>` : "") +
        (meta.geyser === false ? `<span>geyser <b>off</b></span>` : "") +
        (meta.top_mid ? `<span>top <b>${meta.top_mid}</b></span>` : "") +
        (meta.scan_ms != null ? `<span>scan <b>${fmt.num(meta.scan_ms, 0)}ms</b></span>` : "") +
        (age ? `<span>last <b>${age}</b></span>` : "") +
        (meta.scan_slot ? `<span>slot <b>${fmt.num(meta.scan_slot, 0)}</b></span>` : "") +
        (meta.min_usd != null ? `<span>floor <b>${fmt.usd(meta.min_usd, 4)}</b></span>` : "") +
        (hopLabs ? `<span>routePlan <b>${hopLabs}</b></span>` : "") +
        (meta.mode ? `<span>mode <b>${meta.mode}</b></span>` : "") +
        (a.error ? `<span style="color:var(--red)">${a.error}</span>` : "");
    }
    const mix = meta.venue_mix || [];
    const track = $("sol-ar-mix-track");
    const keys = $("sol-ar-mix-keys");
    if (track) {
      track.innerHTML = mix.length
        ? mix.map((p) => {
            const c = SOL_VENUE_COLORS[(p.venue || "").toLowerCase()] || "#64748b";
            return `<span style="width:${Math.max(2, p.pct || 0)}%;background:${c}" title="${p.venue} ${p.n}"></span>`;
          }).join("")
        : `<span style="width:100%;background:#1e293b"></span>`;
    }
    if (keys) {
      keys.innerHTML = mix.slice(0, 5).map((p) => {
        const c = SOL_VENUE_COLORS[(p.venue || "").toLowerCase()] || "#64748b";
        return `<span><i style="display:inline-block;width:7px;height:7px;border-radius:2px;background:${c};margin-right:4px"></i>${p.venue} <b>${p.n}</b></span>`;
      }).join("") || `<span class="dim">no +EV venues this cycle</span>`;
    }
    solArCache = (a.opps || []).filter((o) => Number(o.net_usd) > 0 && !o.same_pool);
    renderSolArb();
    const nbody = $("sol-arb-near-table") && $("sol-arb-near-table").querySelector("tbody");
    const nempty = $("sol-arb-near-empty");
    const near = a.near || [];
    const skipDbg = $("sol-ar-skip-debug-n");
    if (skipDbg) skipDbg.textContent = String(meta.skipped != null ? meta.skipped : near.length);
    if (nempty) {
      if (near.length) nempty.style.display = "none";
      else {
        nempty.style.display = "block";
        nempty.textContent = (meta.last_scan || a.last_scan)
          ? "no debug skips this scan"
          : "waiting for first pool-account scan…";
      }
    }
    if (nbody) {
      nbody.innerHTML = near.slice(0, 8).map((o) => {
        const gap = o.gap_usd != null ? o.gap_usd : o.net_usd;
        const why = o.skip_reason || (o.net_usd != null && o.net_usd <= 0 ? "neg_net" : "below_floor");
        const hop = (o.hop_src || o.labels || []).join("→") || "jup";
        return `<tr class="arb-near-row" title="${hop}">
          <td>${solVenueBadge(o)}</td>
          <td><b>${o.mid || o.path || "?"}</b></td>
          <td class="dim">${fmt.usd(o.net_usd, 4)}</td>
          <td style="color:var(--red)">${fmt.usd(gap, 4)}</td>
          <td class="dim">${why}</td>
        </tr>`;
      }).join("");
    }
    const st = a.stats || {};
    const cov = $("sol-arb-stats");
    const covNote = $("sol-ar-cov-note");
    if (covNote) covNote.textContent = (meta.quote_src || (meta.dexes || ["local"]).slice(0, 3).join("+")) || "local";
    if (cov) {
      const byDex = meta.by_dex || st.by_dex || {};
      const dexBits = Object.entries(byDex).slice(0, 8).map(([k, v]) => `${k}:${v}`).join(" ");
      const toks = (meta.tokens || (a.universe && a.universe.tokens) || []).slice(0, 8).join(" ");
      const sample = meta.sample_route || {};
      const hopLabs = (sample.labels || []).join(" → ");
      const qmix = meta.quote_src_mix || {};
      const mixBits = Object.entries(qmix).map(([k, v]) => `${k}:${v}`).join(" ");
      cov.innerHTML = [
        `<span>net <b>solana mainnet</b></span>`,
        `<span>pairs <b>${fmt.num(meta.pairs != null ? meta.pairs : st.pairs, 0)}</b></span>`,
        `<span>jobs local/jup <b>${fmt.num(meta.local_jobs != null ? meta.local_jobs : 0, 0)}/${fmt.num(meta.jup_jobs != null ? meta.jup_jobs : 0, 0)}</b></span>`,
        `<span>quoted <b>${fmt.num(meta.quoted != null ? meta.quoted : (st.quoted != null ? st.quoted : meta.quotes), 0)}</b></span>`,
        `<span>quote_src <b>${mixBits || meta.quote_src || "—"}</b></span>`,
        `<span>local vs jup <b>${fmt.num(meta.local_n, 0)}/${fmt.num(meta.jup_n, 0)}</b></span>`,
        (meta.pools_decoded != null ? `<span>pools decoded <b>${fmt.num(meta.pools_decoded, 0)}/${fmt.num(meta.pools_watch != null ? meta.pools_watch : 11, 0)}</b></span>` : ""),
        (meta.geyser === false ? `<span>geyser <b>off (account poll)</b></span>` : ""),
        (dexBits ? `<span>graph <b>${dexBits}</b></span>` : ""),
        (toks ? `<span>mids <b>${toks}</b></span>` : ""),
        `<span>skipped <b>${fmt.num(meta.skipped != null ? meta.skipped : st.skipped, 0)}</b></span>`,
        (meta.skipped_same_pool != null ? `<span>same-pool <b>${fmt.num(meta.skipped_same_pool, 0)}</b></span>` : ""),
        (meta.quote_errors ? `<span style="color:var(--red)">quote fail <b>${meta.quote_errors}</b></span>` : ""),
        (meta.min_usd != null ? `<span>floor <b>${fmt.usd(meta.min_usd, 4)}</b></span>` : ""),
        (meta.tip_usd != null ? `<span>tip <b>${fmt.usd(meta.tip_usd, 4)}</b></span>` : ""),
        (hopLabs ? `<span>sample routePlan <b>${hopLabs}</b></span>` : ""),
        (meta.last_scan ? `<span>last scan <b>${fmt.age(meta.last_scan)}</b></span>` : ""),
        `<span>submit <b>${meta.submit_gate || "blocked"}</b></span>`,
      ].filter(Boolean).join("");
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
        `<span>keep live <b>${bc.keep_live ? "ON" : "off"}</b></span>` +
        `<span>armed <b>${bc.arm_note || (bc.armed ? "LIVE" : "no")}</b></span>` +
        `<span>liq hist <b>${fmt.num(sum.n_liq, 0)}</b></span>` +
        `<span>MEV hist <b>${fmt.num(sum.n_mev, 0)}</b></span>` +
        (bc.liq_contract ? `<span>liq <b>${String(bc.liq_contract).slice(0, 10)}…</b></span>` : `<span>liq <b>unset</b></span>`) +
        (bc.arb_contract ? `<span>arb <b>${String(bc.arb_contract).slice(0, 10)}…</b></span>` : `<span>arb <b>unset</b></span>`);
    }
    const pills = $("sol-bc-mode-pills");
    if (pills) {
      pills.innerHTML =
        `<span class="bc-pill ${bc.sim_only ? "on" : ""}">sim ${bc.sim_only ? "ON" : "off"}</span>` +
        `<span class="bc-pill ${bc.keep_live ? "keep" : ""}">keep live ${bc.keep_live ? "auto-renew" : "off"}</span>` +
        `<span class="bc-pill ${bc.armed ? "live" : ""}">${bc.arm_note || (bc.armed ? "armed LIVE" : "not armed")}</span>` +
        `<span class="bc-pill ${bc.edge_bias ? "on" : ""}">edge ${bc.edge_bias ? "on" : "off"}</span>` +
        `<span class="bc-pill ${ready.liq ? "on" : "warn"}">liq ${ready.liq ? "ready" : "blocked"}</span>` +
        `<span class="bc-pill ${ready.arb ? "on" : "warn"}">MEV ${ready.arb ? "ready" : "blocked"}</span>`;
    }
    const btnSim = $("sol-btn-sim");
    const btnKeep = $("sol-btn-keep-live");
    const btnArm = $("sol-btn-arm");
    const btnEdge = $("sol-btn-edge");
    if (btnSim) {
      btnSim.classList.toggle("on-sim", !!bc.sim_only);
      btnSim.textContent = bc.sim_only ? "Sim ON" : "Sim-only";
    }
    if (btnKeep) {
      btnKeep.classList.toggle("on-keep", !!bc.keep_live);
      btnKeep.textContent = bc.keep_live ? "Keep Live ON" : "Keep Live";
    }
    if (btnArm) {
      btnArm.classList.toggle("on-arm", !!bc.armed);
      btnArm.textContent = bc.armed ? "Disarm LIVE" : "Arm LIVE";
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
        `<tr><td>${fmt.ts(h.ts)}</td>
          <td><span class="sol-kind ${h.kind || ""}">${h.kind || ""}</span></td>
          <td>${h.stage || ""}</td>
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

  const setSolFundStatus = (msg, cls) => {
    const st = $("sol-fund-status");
    if (!st) return;
    st.className = cls || "";
    if (msg && /<a /.test(String(msg))) st.innerHTML = msg;
    else st.textContent = msg || "";
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
    const copyFund = $("sol-btn-copy-fund");
    if (copyFund && !copyFund.__bound) {
      copyFund.__bound = true;
      copyFund.addEventListener("click", () => {
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
          setSolFundStatus("sponsor/bot pubkeys missing — restart dashboard", "err");
          return;
        }
        navigator.clipboard.writeText(text).then(() => {
          setSolFundStatus("copied: funder → sponsor + bot amounts and addresses");
        }).catch(() => {
          setSolFundStatus(text.replace(/\n/g, " · "));
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

  const BOT_GROUPS = [
    { id: "sense", label: "sense", keys: ["mempool", "prices"] },
    { id: "hunt", label: "hunt", keys: ["sweep", "competitors", "arb"] },
    { id: "act", label: "act", keys: ["broadcast", "funds", "intel"] },
  ];

  function renderBotsFleet(opts) {
    const el = $(opts.listId);
    if (!el) return;
    const hx = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
    ));
    const bots = opts.bots || {};
    const labels = opts.labels || {};
    const roles = opts.roles || {};
    const funds = opts.funds || {};
    const wallets = opts.wallets || {};
    const bc = opts.bc || {};
    const ready = bc.ready || {};
    const sim = !!bc.sim_only;
    const armed = !!bc.armed;
    const gates = !!(ready.liq || ready.arb);

    const unit = opts.unit || "ETH";
    const balKey = opts.balKey || "eth";
    const walletLow = opts.walletLow || ((n, f) => {
      const bal = f[balKey];
      if (n === "funder") return false;
      if (bal == null) return false;
      if (n === "sponsor") {
        const tgt = Number(bc.sponsor_target_eth || bc.sponsor_target_sol) || (balKey === "sol" ? 0.08 : 0.03);
        return bal < tgt * 0.5;
      }
      return balKey === "sol" ? bal < 0.02 : bal < 0.001;
    });
    const unfunded = ["sponsor", "bot"].some((n) => walletLow(n, funds[n] || {}));

    const toneOf = (key, raw) => {
      const st = String(raw || "idle").toLowerCase();
      if (key === "funds" && unfunded) return { label: "unfunded", cls: "unfunded", st: "error" };
      if (key === "broadcast") {
        if (st === "error") return { label: "blocked", cls: "blocked", st: "error" };
        if (armed && !sim) return { label: "live", cls: "live", st: st === "running" ? "running" : "ok" };
        if (sim) return { label: "sim", cls: "sim", st: st === "running" ? "running" : (st === "ok" ? "ok" : st) };
        if (!gates) return { label: "blocked", cls: "blocked", st: st === "error" ? "error" : "idle" };
      }
      if (st === "ok") return { label: "ready", cls: "ready", st: "ok" };
      if (st === "running") return { label: "live", cls: "running", st: "running" };
      if (st === "error") return { label: "blocked", cls: "blocked", st: "error" };
      return { label: "idle", cls: "idle", st: "idle" };
    };

    let nOk = 0, nRun = 0, nErr = 0, nIdle = 0;
    Object.keys(labels).forEach((k) => {
      const t = toneOf(k, (bots[k] || {}).status);
      if (t.cls === "ready" || t.cls === "sim") nOk += 1;
      else if (t.cls === "running" || t.cls === "live") nRun += 1;
      else if (t.cls === "blocked" || t.cls === "unfunded") nErr += 1;
      else nIdle += 1;
    });
    const total = Object.keys(labels).length || 8;
    const setTxt = (id, v) => { const n = $(id); if (n) n.textContent = v; };
    if (opts.counts) {
      setTxt(opts.counts.ok, String(nOk));
      setTxt(opts.counts.run, String(nRun));
      setTxt(opts.counts.err, String(nErr));
      setTxt(opts.counts.idle, String(nIdle));
    }
    const tag = $(opts.tagId);
    if (tag) tag.textContent = nErr ? `${nErr} blocked` : `${nOk}/${total} ready`;

    let pressure = "idle", pLabel = "idle";
    if (nErr) { pressure = "hot"; pLabel = "blocked"; }
    else if (nRun) { pressure = "busy"; pLabel = "running"; }
    else if (nOk === total) { pressure = "quiet"; pLabel = "ready"; }
    const pr = $(opts.pressureId);
    if (pr) {
      pr.textContent = pLabel;
      pr.className = "bt-pressure-badge " + pressure;
    }

    const mode = $(opts.modeId);
    if (mode) {
      mode.innerHTML =
        `<span class="bt-pill ${sim ? "sim" : "idle"}">sim ${sim ? "ON" : "off"}</span>` +
        `<span class="bt-pill ${bc.keep_live ? "live" : "idle"}">keep live ${bc.keep_live ? "ON" : "off"}</span>` +
        `<span class="bt-pill ${armed ? "live" : "idle"}">${bc.arm_note || (armed ? "armed LIVE" : "not armed")}</span>` +
        `<span class="bt-pill ${gates ? "ready" : "blocked"}">${gates ? "gates clear" : "gates blocked"}</span>`;
    }

    const wal = $(opts.walletsId);
    if (wal) {
      const tags = { funder: "capital", sponsor: "tips", bot: "fee payer" };
      wal.innerHTML = ["funder", "sponsor", "bot"].map((n) => {
        const f = funds[n] || {};
        const pk = wallets[n] || f.pubkey || "";
        const raw = f[balKey];
        const bal = raw != null ? fmt.num(raw, 4) + " " + unit : "--";
        const low = walletLow(n, f);
        const short = pk ? `${pk.slice(0, 4)}…${pk.slice(-4)}` : "unset";
        const addr = pk
          ? `<span class="mono copy" data-addr="${hx(pk)}" title="click to copy">${hx(short)}</span>`
          : `<span class="mono dim">unset</span>`;
        return `<div class="bt-wal${low ? " low" : ""}">
          <div class="bt-wal-h"><span class="bt-wal-name">${n}</span><span class="bt-role">${tags[n]}</span></div>
          <div class="bt-wal-addr">${addr}</div>
          <div class="bt-wal-bal">${hx(bal)}${low ? `<span class="bt-pill unfunded">unfunded</span>` : ""}</div>
        </div>`;
      }).join("");
    }

    el.innerHTML = BOT_GROUPS.map((g) => {
      const tiles = g.keys.map((k) => {
        const b = bots[k] || {};
        const t = toneOf(k, b.status);
        const age = b.last ? fmt.age(b.last) + " ago" : "never";
        const msg = String(b.msg || "").trim();
        return `<div class="bot tone-${t.cls}">
          <div class="bt-head">
            <span class="st ${t.st}"></span>
            <div class="bt-id">
              <div class="b-name">${hx(labels[k] || k)}</div>
              <span class="bt-role">${hx(roles[k] || k)}</span>
            </div>
            <span class="bt-pill ${t.cls}">${t.label}</span>
          </div>
          <div class="bt-fields"><span class="b-last"><i>seen</i><b>${hx(age)}</b></span></div>
          <div class="b-msg${msg ? "" : " is-empty"}" title="${hx(msg)}">${msg ? hx(msg) : "—"}</div>
        </div>`;
      }).join("");
      return `<div class="bt-group">
        <div class="bt-group-h">${g.label}</div>
        <div class="bt-group-grid">${tiles}</div>
      </div>`;
    }).join("");
  }

  const updateBots = (s) => {
    renderBotsFleet({
      listId: "bots-list",
      tagId: "bots-tag",
      pressureId: "bots-pressure",
      modeId: "bots-mode",
      walletsId: "bots-wallets",
      counts: { ok: "bots-n-ok", run: "bots-n-run", err: "bots-n-err", idle: "bots-n-idle" },
      bots: (s || {}).bots,
      labels: {
        mempool: "Mempool Watcher", prices: "Oracle / Prices", funds: "Funds Balances",
        sweep: "HF Opportunity Sweep", competitors: "Competitor Watch", arb: "DEX Arb Scanner",
        intel: "Learning / Intel", broadcast: "Broadcast Submit",
      },
      roles: {
        mempool: "watch", prices: "oracle", funds: "wallets", sweep: "HF",
        competitors: "liq", arb: "DEX", intel: "learn", broadcast: "submit",
      },
      funds: (s || {}).funds,
      wallets: (s || {}).wallets,
      bc: (s || {}).broadcast,
    });
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
  let opProto = "all";
  let opCache = [];
  let opLastMeta = {};
  const OP_PAIR_COLORS = ["#22c55e", "#22d3ee", "#f59e0b", "#a78bfa", "#ef4444", "#3b82f6", "#ec4899", "#14b8a6"];

  const protoId = (o) => {
    const p = String((o && (o.protocol_id || o.protocol)) || "").toLowerCase();
    if (!p || p === "v3" || p === "v4" || p === "aave") return "aave";
    if (p.indexOf("spark") >= 0) return "spark";
    if (p.indexOf("compound") >= 0 || p === "comet") return "compound";
    if (p.indexOf("morpho") >= 0) return "morpho";
    return p;
  };
  const protoLabel = (o) => {
    const map = { aave: "Aave", spark: "Spark", compound: "Compound", morpho: "Morpho" };
    const lab = String((o && (o.protocol_label || o.protocol)) || "");
    if (lab && !["v3", "v4", "aave"].includes(lab.toLowerCase())) return lab;
    return map[protoId(o)] || lab || protoId(o);
  };
  const protoPill = (o) => {
    const id = protoId(o);
    return `<span class="pill proto proto-${id}">${protoLabel(o)}</span>`;
  };

  const oppHf = (o) => {
    const n = Number(o && o.hf);
    if (isNaN(n)) return null;
    return n > 1e9 ? n / 1e18 : n;
  };

  const liqFlagBits = (o) => {
    const bits = [];
    if (o.actionable && o.submit === "live") bits.push(`<span class="pill ok">LIVE</span>`);
    else if (o.actionable) bits.push(`<span class="pill warn">+EV sim</span>`);
    if (o.submit === "blocked")
      bits.push(`<span class="pill blocked" title="${o.submit_reason || "blocked"}">blocked</span>`);
    else if (o.submit === "sim")
      bits.push(`<span class="pill" title="${o.submit_reason || "sim-only"}">sim</span>`);
    if (o.race || o.contested)
      bits.push(`<span class="pill warn" title="mempool or recent competitor">race</span>`);
    if (o.recent_competitor) bits.push(`<span class="pill">comp</span>`);
    if (o.edge) bits.push(`<span class="pill accent">${o.edge}</span>`);
    if (o.flash_fee_bps != null && Number(o.flash_fee_bps) > 0)
      bits.push(`<span class="pill" title="${o.flash_note || "Aave V3 flashLoan"}">${fmt.num(o.flash_fee_bps, 0)} bps flash</span>`);
    if (o.submit === "blocked" && o.live_block_reason)
      bits.push(`<span class="pill blocked" title="${o.live_block_reason}">venue</span>`);
    if (o.leftover) bits.push(`<span class="pill" title="${o.leftover}">leftover</span>`);
    const user = o.user || "";
    if (user) {
      bits.push(`<a class="op-link" href="https://etherscan.io/address/${user}" target="_blank" rel="noopener">↗</a>`);
      bits.push(`<span class="mono copy op-link" data-addr="${user}" title="${user}">copy</span>`);
    }
    return bits.join(" ") || `<span class="dim">—</span>`;
  };

  const renderOppsFeed = () => {
    const rows = opCache.filter((o) => {
      if (opProto !== "all" && protoId(o) !== opProto) return false;
      const hf = oppHf(o);
      if (opFilter === "all") return true;
      if (opFilter === "edge") return !!o.edge;
      if (opFilter === "profit") return Number(o.net_usd != null ? o.net_usd : o.profit_usd) > 0;
      if (opFilter === "race") return !!(o.race || o.contested || o.recent_competitor);
      if (opFilter === "hf095") return hf != null && hf < 0.95;
      if (opFilter === "hf1") return hf != null && hf < 1.0;
      return true;
    });
    const note = $("op-feed-note");
    if (note) note.textContent = `${rows.length}/${opCache.length} · multi-protocol · skip dust`;
    const empty = $("opps-empty");
    const body = $("opps-table") && $("opps-table").querySelector("tbody");
    if (!body) return;
    if (empty) {
      if (rows.length) {
        empty.style.display = "none";
        empty.classList.remove("err");
      } else {
        empty.style.display = "block";
        const m = opLastMeta || {};
        if (m.errors && m.errors.length) {
          empty.classList.add("err");
          empty.textContent = "sweep error: " + m.errors[0];
        } else if (m.last_scan) {
          empty.classList.remove("err");
          const skipped = m.skipped_n != null ? m.skipped_n : "";
          empty.textContent = `no HF<1 +EV this multi-protocol scan · ${fmt.num(m.scanned || 0, 0)} users`
            + (m.n_logs != null ? ` · ${fmt.num(m.n_logs, 0)} logs` : "")
            + (skipped !== "" ? ` · skip ${fmt.num(skipped, 0)}` : "")
            + (m.last_block ? ` · blk ${m.last_block}` : "")
            + (m.last_scan ? ` · ${fmt.age(m.last_scan)} ago` : "")
            + (opProto !== "all" ? ` · filter ${opProto}` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "scanning Aave · Spark · Compound · Morpho for HF<1…";
        }
      }
    }
    body.innerHTML = rows.slice(0, 60).map((o) => {
      const user = o.user || "";
      const short = user ? `${user.slice(0, 6)}…${user.slice(-4)}` : "--";
      const hf = oppHf(o);
      const hfCell = hf == null ? "--" : (hf >= 100 ? "∞" : hf.toFixed(3));
      const pair = `${o.coll_sym || "?"} → ${o.debt_sym || "?"}`;
      const sizes = (o.coll_usd != null || o.debt_usd != null)
        ? `<div class="dim">${fmt.usd(o.coll_usd)} / ${fmt.usd(o.debt_usd)}</div>` : "";
      const net = o.net_usd != null ? o.net_usd : o.profit_usd;
      const netColor = net == null ? "var(--dim)" : net > 0 ? "var(--green)" : "var(--red)";
      return `<tr>
        <td>${protoPill(o)}</td>
        <td class="mono copy" data-addr="${user}" title="${user}">${short}</td>
        <td class="${hfClass(hf)}">${hfCell}</td>
        <td><b>${pair}</b>${sizes}</td>
        <td style="color:var(--amber)">${o.bonus_usd != null ? fmt.usd(o.bonus_usd) : "--"}</td>
        <td style="color:${netColor}"><b>${net != null ? fmt.usd(net) : "--"}</b></td>
        <td>${liqFlagBits(o)}</td>
      </tr>`;
    }).join("");
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
  const opProtoFilters = $("op-proto-filters");
  if (opProtoFilters) {
    opProtoFilters.addEventListener("click", (e) => {
      const btn = e.target.closest(".op-f");
      if (!btn) return;
      opProto = btn.dataset.proto || "all";
      opProtoFilters.querySelectorAll(".op-f").forEach((b) => b.classList.toggle("on", b === btn));
      renderOppsFeed();
    });
  }

  const updateOpps = (s) => {
    const opps = s.opportunities || [];
    const wl = (s.watchlist || []).filter((w) => Number(w.hf) < 1e38);
    const m = s.opportunities_meta || {};
    opLastMeta = m;
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
    setTxt("op-sweep", `${fmt.num(m.scanned != null ? m.scanned : (sweep != null ? sweep : 0), 0)}`);

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
      const gate = m.submit_gate || "blocked";
      meta.innerHTML =
        `<span>Σ net <b style="color:var(--green)">${fmt.usd(m.sum_profit != null ? m.sum_profit : opps.reduce((a, o) => a + (Number(o.net_usd || o.profit_usd) || 0), 0))}</b></span>` +
        `<span>watch <b>${fmt.num(wl.length, 0)}</b></span>` +
        (m.scanned != null ? `<span>scanned <b>${fmt.num(m.scanned, 0)}</b></span>` : "") +
        (m.n_logs != null ? `<span>logs <b>${fmt.num(m.n_logs, 0)}</b></span>` : "") +
        (sweepBot.status ? `<span>sweep <b>${sweepBot.status}</b></span>` : "") +
        `<span>gate <b>${gate}</b></span>` +
        (m.flash_fee_bps != null ? `<span>flash <b>${fmt.num(m.flash_fee_bps, 0)} bps ${m.flash_fee_src || "aave-v3"}</b></span>` : "") +
        (m.protocol_mix && m.protocol_mix.length
          ? `<span>venues <b>${m.protocol_mix.map((p) => `${p.id}×${p.n}`).join(" · ")}</b></span>` : "") +
        (m.last_block ? `<span>blk <b>${m.last_block}</b></span>` : "") +
        (m.last_scan ? `<span>${fmt.age(m.last_scan)} ago</span>` : "") +
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

    const leftoverEl = $("op-leftover");
    if (leftoverEl) {
      if (m.leftovers && m.leftovers.length) {
        leftoverEl.style.display = "";
        const bits = m.leftovers.slice(0, 4);
        leftoverEl.textContent = bits.some((t) => /KIND\(\)|DEPLOY\.md|GenericFlashLiquidator/i.test(String(t)))
          ? bits.join(" · ")
          : "leftover · " + bits.join(" · ");
      } else {
        leftoverEl.style.display = "none";
        leftoverEl.textContent = "";
      }
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
          const collUsd = w.coll_usd != null ? Number(w.coll_usd) : Number(w.coll) / 1e26;
          const debtUsd = w.debt_usd != null ? Number(w.debt_usd) : Number(w.debt) / 1e26;
        const hfCell = hf >= 100 ? "∞" : hf.toFixed(3);
        const urg = hfUrgency(hf);
        const user = w.user || "";
        const short = user ? `${user.slice(0, 10)}…` : "--";
        return `<tr>
          <td>${protoPill(w)}</td>
          <td class="mono copy" data-addr="${user}" title="click to copy">${short}</td>
          <td class="${hfClass(hf)}">${hfCell}</td>
          <td>${fmt.usd(collUsd)}</td>
          <td>${fmt.usd(debtUsd)}</td>
          <td><span class="op-urg ${urg.cls}">${urg.label}</span></td>
        </tr>`;
      }).join("") || `<tr><td colspan="6" class="dim">--</td></tr>`;
    }
  };

  let cpFilter = "all";
  let cpProto = "all";
  let cpCache = [];
  let cpLastMeta = {};

  const renderCompFeed = () => {
    const rows = cpCache.filter((c) => {
      if (cpProto !== "all" && protoId(c) !== cpProto) return false;
      if (cpFilter === "all") return true;
      if (cpFilter === "miss") return !!c.missed_by_us;
      if (cpFilter === "edge") return !!c.edge;
      if (cpFilter === "profit") return (c.net_est_usd != null ? c.net_est_usd : c.est_profit_usd) > 0;
      if (cpFilter === "revert") return c.status === 0;
      return true;
    });
    const note = $("cp-feed-note");
    if (note) note.textContent = `${rows.length}/${cpCache.length} · newest first`;
    const empty = $("comp-empty");
    const body = $("comp-table") && $("comp-table").querySelector("tbody");
    if (!body) return;
    if (empty) {
      if (rows.length) {
        empty.style.display = "none";
        empty.classList.remove("err");
      } else {
        empty.style.display = "block";
        const m = cpLastMeta || {};
        if (m.status && String(m.status).startsWith("err")) {
          empty.classList.add("err");
          empty.textContent = "scan error: " + m.status;
        } else if (m.last_scan || m.last_block) {
          empty.classList.remove("err");
          const from = m.from_block, to = m.to_block || m.last_block;
          empty.textContent = `no liquidations this window (Aave · Spark · Compound · Morpho)`
            + (from && to ? ` · blk ${from}→${to}` : (m.last_block ? ` · blk ${m.last_block}` : ""))
            + (m.window ? ` · ${fmt.num(m.window, 0)} blocks` : "")
            + (m.n_logs != null ? ` · ${fmt.num(m.n_logs, 0)} logs` : "")
            + (m.last_scan ? ` · ${fmt.age(m.last_scan)} ago` : "")
            + (cpProto !== "all" ? ` · filter ${cpProto}` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "scanning Aave · Spark · Compound · Morpho liquidation logs…";
        }
      }
    }
    body.innerHTML = rows.slice(0, 50).map((c) => {
      const pair = `${c.coll_sym || RESERVE_SYMS[+c.coll] || c.coll}→${c.debt_sym || RESERVE_SYMS[+c.debt] || c.debt}`;
      const searcher = c.searcher || "";
      const user = c.user || "";
      const sShort = searcher ? `${searcher.slice(0, 6)}…${searcher.slice(-4)}` : "--";
      const uShort = user ? `${user.slice(0, 6)}…${user.slice(-4)}` : "--";
      const net = c.net_est_usd;
      const netColor = net == null ? "var(--dim)" : net >= 0 ? "var(--green)" : "var(--red)";
      const flags = [];
      if (c.missed_by_us) flags.push(`<span class="cp-flag miss">miss</span>`);
      if (c.edge) flags.push(`<span class="cp-flag edge">edge</span>`);
      if (c.status === 0) flags.push(`<span class="cp-flag revert">revert</span>`);
      const tx = c.tx
        ? `<a href="https://etherscan.io/tx/${c.tx}" target="_blank" rel="noopener" style="color:var(--cyan)">${c.tx.slice(0, 8)}…</a>` +
          ` <span class="mono copy op-link" data-addr="${c.tx}" title="${c.tx}">copy</span>`
        : `<span class="dim">--</span>`;
      return `<tr>
        <td class="dim" title="blk ${c.block || "?"}">${fmt.age(c.ts)}</td>
        <td>${protoPill(c)}</td>
        <td><b>${pair}</b></td>
        <td class="mono copy" data-addr="${searcher}" title="${searcher}">${sShort}</td>
        <td class="mono copy dim" data-addr="${user}" title="${user}">${uShort}</td>
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
  const cpProtoFilters = $("cp-proto-filters");
  if (cpProtoFilters) {
    cpProtoFilters.addEventListener("click", (e) => {
      const btn = e.target.closest(".op-f");
      if (!btn) return;
      cpProto = btn.dataset.proto || "all";
      cpProtoFilters.querySelectorAll(".op-f").forEach((b) => b.classList.toggle("on", b === btn));
      renderCompFeed();
    });
  }

  const PAIR_COLORS = ["#ef4444", "#f59e0b", "#a78bfa", "#22d3ee", "#22c55e", "#3b82f6", "#ec4899", "#14b8a6"];

  const updateCompetitors = (s) => {
    const m = s.competitors_meta || {};
    const hist = s.hist || {};
    cpCache = (s.competitors || []).slice(0, 80);
    cpLastMeta = m;

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
        (m.from_block && m.to_block ? `<span>scan <b>${m.from_block}→${m.to_block}</b></span>` : "") +
        (m.last_scan ? `<span>${fmt.age(m.last_scan)} ago</span>` : "") +
        (m.n_logs != null ? `<span>logs <b>${fmt.num(m.n_logs, 0)}</b></span>` : "") +
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
          <td class="mono copy" title="${t.addr || ""}" data-addr="${t.addr || ""}">${(t.short || (t.addr || "").slice(0, 6) || "--")}…${(t.addr || "").slice(-4)}</td>
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
  let arMetaCache = {};
  let arLastState = {};

  const arbFlag = (o) => {
    const bits = [];
    if (o.actionable && o.submit === "live") bits.push(`<span class="pill ok">LIVE</span>`);
    else if (o.actionable) bits.push(`<span class="pill warn">+EV sim</span>`);
    else if (o.net_usd != null && o.net_usd <= 0)
      bits.push(`<span class="pill warn">gas</span>`);
    if (o.submit === "blocked")
      bits.push(`<span class="pill blocked" title="${o.submit_reason || "blocked"}">blocked</span>`);
    else if (o.submit === "sim")
      bits.push(`<span class="pill" title="${o.submit_reason || "sim-only"}">sim</span>`);
    if (o.cross_dex) bits.push(`<span class="pill accent">cross</span>`);
    if (o.sized) bits.push(`<span class="pill">sized</span>`);
    if (o.learned) bits.push(`<span class="pill accent">learn</span>`);
    if (o.mode === "backrun")
      bits.push(`<span class="pill accent" title="${(o.victim_hash || o.bundle_mode || "backrun").replace(/"/g, "&quot;")}">backrun</span>`);
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
    let rows = arLiveCache.filter((o) => Number(o.net_usd) > 0 && !o.same_pool);
    if (arFilter !== "all") {
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
      note.textContent = rows.length ? `${rows.length} +EV` : "+EV after costs";
    }
    const empty = $("arb-empty");
    if (empty) {
      const m = arMetaCache || {};
      const a = arLastState || {};
      if (rows.length) {
        empty.style.display = "none";
        empty.classList.remove("err");
      } else {
        empty.style.display = "block";
        if (a.error) {
          empty.classList.add("err");
          empty.textContent = "scan error: " + a.error;
        } else if (m.last_scan) {
          empty.classList.remove("err");
          const n = m.pairs || 0;
          const q = m.quoted != null ? m.quoted : m.quotes;
          const skip = m.skipped != null ? m.skipped : arNearCache.length;
          const floor = m.min_usd;
          empty.textContent = `no +EV this scan · ${fmt.num(n, 0)} pairs`
            + (q != null ? ` · ${fmt.num(q, 0)} quotes` : "")
            + (skip != null ? ` · skip ${fmt.num(skip, 0)}` : "")
            + (floor != null ? ` · floor ${fmt.usd(floor)}` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "backrun+lst · scoring LST/stables (Aave flash)…";
        }
      }
    }
    const body = $("arb-table") && $("arb-table").querySelector("tbody");
    if (!body) return;
    body.innerHTML = rows.slice(0, 40).map((o) => {
      const netColor = o.net_usd > 0 ? "var(--green)" : "var(--dim)";
      const planTip = o.plan ? JSON.stringify(o.plan).replace(/"/g, "&quot;") : (o.flash_full || "");
      return `<tr>
        <td>${venueBadge(o)}</td>
        <td><b>${o.mid || "?"}</b></td>
        <td class="mono dim" title="${planTip}">${o.route || "--"}</td>
        <td>${fmt.num(o.hops || 2, 0)}</td>
        <td>${fmt.num(o.borrow_weth, 4)}</td>
        <td style="color:var(--amber)">${fmt.usd(o.profit_usd)}</td>
        <td class="dim">${fmt.usd(o.gas_usd)}</td>
        <td style="color:${netColor}"><b>${fmt.usd(o.net_usd)}</b></td>
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
    arLiveCache = (a.opps || []).filter((o) => Number(o.net_usd) > 0 && !o.same_pool);
    arNearCache = a.near || [];
    arMetaCache = m;
    arLastState = a;

    const pressure = m.pressure || "idle";
    const badge = $("ar-pressure");
    if (badge) {
      badge.textContent = pressure;
      badge.className = "ar-pressure-badge " + pressure;
    }
    const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    setTxt("ar-live", fmt.num(m.live, 0));
    setTxt("ar-actionable", fmt.num(m.actionable, 0));
    setTxt("ar-best-net", m.best_net_usd != null && m.live ? fmt.usd(m.best_net_usd) : "--");
    setTxt("ar-skip-n", fmt.num(m.skipped != null ? m.skipped : (m.near || 0), 0));
    setTxt("ar-skip-debug-n", fmt.num(m.skipped != null ? m.skipped : arNearCache.length, 0));

    const meta = $("arb-meta");
    if (meta) {
      const uni = a.universe || {};
      const dexes = (m.dexes || uni.venues || []).join("+") || "—";
      const gate = m.submit_gate || "blocked";
      const gateColor = gate === "live" ? "var(--green)" : gate === "sim" ? "var(--amber)" : "var(--red)";
      const age = m.last_scan ? fmt.age(m.last_scan) : (a.last_scan ? fmt.age(a.last_scan) : null);
      meta.innerHTML =
        `<span>pairs <b>${fmt.num(m.pairs != null ? m.pairs : uni.pairs, 0)}</b></span>` +
        `<span>dexes <b>${dexes}</b></span>` +
        `<span>submit <b style="color:${gateColor}">${gate}</b></span>` +
        `<span>cross <b style="color:var(--violet)">${fmt.num(m.cross_dex, 0)}</b></span>` +
        (m.top_mid ? `<span>top <b>${m.top_mid}</b></span>` : "") +
        (m.gas_gwei != null ? `<span>gas <b>${fmt.num(m.gas_gwei, 2)} gwei</b></span>` : "") +
        (m.scan_ms != null ? `<span>scan <b>${fmt.num(m.scan_ms, 0)}ms</b></span>` : "") +
        (age ? `<span>last <b>${age}</b></span>` : "") +
        (m.mode ? `<span>mode <b>${m.mode}</b></span>` : "") +
        (m.last_victim ? `<span>victim <b class="mono">${m.last_victim}</b></span>` : "") +
        (m.quote_src ? `<span>quotes <b>${m.quote_src}</b></span>` : "") +
        (m.live != null ? `<span>live <b>${fmt.num(m.live, 0)}</b></span>` : "") +
        (m.skipped != null ? `<span>skipped <b>${fmt.num(m.skipped, 0)}</b></span>` : "") +
        (m.flash_fee_bps != null ? `<span>flash <b>${fmt.num(m.flash_fee_bps, 0)} bps ${m.flash_fee_src || "aave"}</b></span>` : "") +
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
    if (nearEmpty) {
      if (arNearCache.length) nearEmpty.style.display = "none";
      else {
        nearEmpty.style.display = "block";
        nearEmpty.textContent = m.last_scan || a.last_scan
          ? "no debug skips this scan"
          : "waiting for first quote pass…";
      }
    }
    if (nearBody) {
      nearBody.innerHTML = arNearCache.slice(0, 8).map((o) => {
        const gap = o.gap_usd != null ? o.gap_usd : o.net_usd;
        const why = o.skip_reason || (o.net_usd != null && o.net_usd <= 0 ? "neg_net" : "below_floor");
        const tip = o.submit_reason ? String(o.submit_reason).replace(/"/g, "&quot;") : "";
        return `<tr class="arb-near-row" title="${tip}">
          <td>${venueBadge(o)}</td>
          <td><b>${o.mid || "?"}</b></td>
          <td class="dim">${fmt.usd(o.net_usd)}</td>
          <td style="color:var(--red)">${fmt.usd(gap)}</td>
          <td class="dim">${why}</td>
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
      const kindBits = Object.entries(m.by_kind || st.by_kind || {}).map(([k, v]) => `${k}:${v}`).join(" ");
      const toks = (m.tokens || (a.universe || {}).tokens || []).slice(0, 8).join(" ");
      cov.innerHTML = [
        `<span>net <b>ethereum mainnet</b></span>`,
        `<span>pairs <b>${fmt.num(m.pairs != null ? m.pairs : st.pairs, 0)}</b></span>`,
        `<span>routes <b>${fmt.num(m.routes != null ? m.routes : st.routes, 0)}</b></span>`,
        `<span>flash pools <b>${fmt.num(m.flash_pools != null ? m.flash_pools : st.flash_pools, 0)}</b></span>`,
        `<span>jobs <b>${fmt.num(m.jobs != null ? m.jobs : st.jobs, 0)}</b></span>`,
        `<span>screened <b>${fmt.num(m.screened != null ? m.screened : st.screened, 0)}</b></span>`,
        `<span>quoted <b>${fmt.num(m.quoted != null ? m.quoted : (st.quoted != null ? st.quoted : st.quotes), 0)}</b></span>`,
        (dexBits ? `<span>graph <b>${dexBits}</b></span>` : ""),
        (kindBits ? `<span>kind <b>${kindBits}</b></span>` : ""),
        (toks ? `<span>mids <b>${toks}</b></span>` : ""),
        (st.gas_gwei_live != null
          ? `<span>live gas <b>${fmt.num(st.gas_gwei_live, 3)} gwei</b></span>` : ""),
        `<span>skipped <b>${fmt.num(m.skipped != null ? m.skipped : st.skipped, 0)}</b></span>`,
        (m.quote_src ? `<span>quotes via <b>${m.quote_src}</b></span>` : ""),
        (m.last_victim ? `<span>last victim <b>${m.last_victim}</b></span>` : ""),
        (m.flash_fee_bps != null
          ? `<span>flash fee <b>${fmt.num(m.flash_fee_bps, 0)} bps ${m.flash_fee_src || "aave-v3"}</b></span>` : ""),
        (m.min_usd != null ? `<span>floor <b>${fmt.usd(m.min_usd)}</b></span>` : ""),
        `<span>best margin <b style="color:${best < 0 ? "var(--amber)" : "var(--green)"}">${fmt.num(best, 5)} WETH</b></span>`,
        (m.last_scan ? `<span>last scan <b>${fmt.age(m.last_scan)}</b></span>` : ""),
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
      else if (bc.armed && (ready.liq || ready.arb)) {
        pressure = "hot";
        label = label || (bc.keep_live ? "armed · auto-renew" : "armed live");
      }
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
      (bc.arb_contract ? `<span>arb <b>${bc.arb_contract.slice(0, 10)}…</b></span>` : "") +
      `<span>keep live <b>${bc.keep_live ? "ON" : "off"}</b></span>` +
      `<span>${bc.arm_note || (bc.armed ? "armed" : "not armed")}</span>`;

    const pills = $("bc-mode-pills");
    if (pills) {
      pills.innerHTML =
        `<span class="bc-pill ${bc.sim_only ? "on" : ""}">sim ${bc.sim_only ? "ON" : "off"}</span>` +
        `<span class="bc-pill ${bc.keep_live ? "keep" : ""}">keep live ${bc.keep_live ? "auto-renew" : "off"}</span>` +
        `<span class="bc-pill ${bc.armed ? "live" : ""}">${bc.arm_note || (bc.armed ? "armed LIVE" : "not armed")}</span>` +
        `<span class="bc-pill ${bc.edge_bias ? "on" : ""}">edge ${bc.edge_bias ? "on" : "off"}</span>` +
        `<span class="bc-pill ${ready.liq && ready.arb ? "ok" : "warn"}">${ready.liq && ready.arb ? "gates clear" : "gates blocked"}</span>`;
    }

    const btnSim = $("btn-sim");
    const btnKeep = $("btn-keep-live");
    const btnArm = $("btn-arm");
    const btnEdge = $("btn-edge");
    if (btnSim) {
      btnSim.classList.toggle("on-sim", !!bc.sim_only);
      btnSim.textContent = bc.sim_only ? "Sim ON" : "Sim-only";
    }
    if (btnKeep) {
      btnKeep.classList.toggle("on-keep", !!bc.keep_live);
      btnKeep.textContent = bc.keep_live ? "Keep Live ON" : "Keep Live";
    }
    if (btnArm) {
      btnArm.classList.toggle("on-arm", !!bc.armed);
      btnArm.textContent = bc.armed ? "Disarm LIVE" : "Arm LIVE";
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
  const CHAIN_TABS = ["eth", "sol"];
  const TAB_HINT = {
    eth: "Ethereum workspace · multi-protocol lending + Aave flash",
    sol: "Solana workspace · Solend liq + Jupiter arb",
  };
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
      if (d.arb) {
        window.__lastState = window.__lastState || {};
        window.__lastState.sol = window.__lastState.sol || {};
        const prev = window.__lastState.sol.arb || {};
        window.__lastState.sol.arb = {
          ...prev,
          opps: d.arb.opps != null ? d.arb.opps : prev.opps,
          near: d.arb.near != null ? d.arb.near : prev.near,
          stats: d.arb.stats || prev.stats,
          error: d.arb.error,
          last_scan: d.arb.last_scan,
          universe: d.arb.universe || prev.universe,
          meta: { ...(prev.meta || {}), ...(d.arb.meta || {}) },
        };
        if (activeTab === "sol") updateSolArb(window.__lastState.sol, window.__lastState.hist);
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
    activeTab = CHAIN_TABS.includes(tab) ? tab : "eth";
    try { localStorage.setItem(TAB_KEY, activeTab); } catch (e) { /* ignore */ }
    try {
      const h = "#" + activeTab;
      if (location.hash !== h) history.replaceState(null, "", h);
    } catch (e) { /* ignore */ }

    CHAIN_TABS.forEach((id) => {
      const panel = $("tab-" + id);
      const btn = $("tab-btn-" + id);
      const on = activeTab === id;
      if (panel) {
        panel.classList.toggle("active", on);
        panel.hidden = !on;
      }
      if (btn) {
        btn.classList.toggle("on", on);
        btn.setAttribute("aria-selected", on ? "true" : "false");
      }
    });
    const hint = $("chain-tabs-hint");
    const chainLabel = $("p-chain-label");
    if (hint) hint.textContent = TAB_HINT[activeTab] || TAB_HINT.eth;
    if (chainLabel) {
      chainLabel.textContent = activeTab === "eth" ? "ETH mainnet" : "SOL mainnet";
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
    try {
      const hash = (location.hash || "").replace(/^#/, "").toLowerCase();
      if (CHAIN_TABS.includes(hash)) saved = hash;
      else saved = localStorage.getItem(TAB_KEY) || "eth";
    } catch (e) { saved = "eth"; }
    setChainTab(CHAIN_TABS.includes(saved) ? saved : "eth");
    if (!window.__tabHashBound) {
      window.__tabHashBound = true;
      window.addEventListener("hashchange", () => {
        const hash = (location.hash || "").replace(/^#/, "").toLowerCase();
        if (CHAIN_TABS.includes(hash) && hash !== activeTab) setChainTab(hash);
      });
    }
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

  /* ------------------------------------------------ web3 / wallet (ETH + SOL, independent) */
  const SOL_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp";
  const SOL_SNAP_ID = "npm:@metamask/solana-wallet-snap";
  const MM_RDNS = { "io.metamask": 1, "io.metamask.flask": 1, "io.metamask.mmi": 1 };
  const SOL_CAIP_METHODS = [
    "signAndSendTransaction", "signTransaction", "signMessage", "signAllTransactions", "signIn",
    "solana_signAndSendTransaction", "solana_signTransaction", "solana_signMessage",
  ];
  const EIP155_CAIP_METHODS = [
    "eth_sendTransaction", "personal_sign", "eth_signTypedData_v4", "eth_getBalance",
    "eth_call", "eth_blockNumber", "eth_getTransactionCount", "wallet_watchAsset",
  ];
  const ethWallet = { provider: null, addr: null, eip1193: null };
  const solWallet = {
    kind: null, name: "", pubkey: null, provider: null, stdWallet: null, stdAccount: null, caipEth: null,
  };
  window.__ethWallet = ethWallet;
  window.__solWallet = solWallet;

  const stdWallets = [];
  try {
    window.addEventListener("wallet-standard:register-wallet", (ev) => {
      try {
        const cb = ev.detail;
        if (typeof cb === "function") {
          cb({ register: (w) => { if (w && stdWallets.indexOf(w) < 0) stdWallets.push(w); } });
        }
      } catch (_) { /* ignore */ }
    });
    window.dispatchEvent(new Event("wallet-standard:app-ready"));
  } catch (_) { /* ignore */ }

  const eip6963 = [];
  try {
    window.addEventListener("eip6963:announceProvider", (ev) => {
      const d = ev && ev.detail;
      if (!d || !d.provider || !d.info) return;
      const rdns = String(d.info.rdns || "");
      if (!eip6963.some((x) => x.info && x.info.rdns === rdns && x.provider === d.provider)) {
        eip6963.push(d);
      }
    });
    window.dispatchEvent(new Event("eip6963:requestProvider"));
  } catch (_) { /* ignore */ }

  const walletErr = (e) => String((e && (e.message || e.data || e.error)) || e || "error").slice(0, 180);

  const setEthFundStatus = (msg, cls) => {
    const st = $("fund-status");
    if (!st) return;
    st.textContent = msg || "";
    st.className = cls || "";
  };

  const rpcCode = (e) => {
    if (!e || typeof e !== "object") return null;
    if (typeof e.code === "number") return e.code;
    if (e.error && typeof e.error.code === "number") return e.error.code;
    return null;
  };
  const isUserReject = (e) => {
    const c = rpcCode(e);
    const m = String((e && (e.message || e.reason || (e.error && e.error.message))) || e || "").toLowerCase();
    return c === 4001 || c === 5001 || /user rejected|rejected the request|denied request|user denied|rejected by user|request rejected|user cancelled|user canceled/.test(m);
  };
  const rejectErr = () => new Error("You rejected the MetaMask Solana permission");
  const noSolErr = () => new Error("Update MetaMask and enable Solana in Settings → Multichain, then click Connect again");
  const isSolAddr = (pk) => {
    const s = String(pk || "");
    return !!s && s.indexOf("0x") !== 0 && s.length >= 32 && s.length <= 48 && s.indexOf(":") < 0;
  };
  const toSolPk = (pk) => {
    if (!pk) return "";
    if (typeof pk.toBase58 === "function") return pk.toBase58();
    if (pk instanceof Uint8Array) {
      const w3 = window.solanaWeb3;
      if (w3) {
        try { return new w3.PublicKey(pk).toBase58(); } catch (_) { /* ignore */ }
      }
    }
    const s = String(pk.address || pk.publicKey || pk);
    if (s.indexOf("solana:") === 0) {
      const parts = s.split(":");
      return parts[parts.length - 1] || "";
    }
    return s;
  };

  const unwrapEip1193 = (p) => {
    if (!p) return null;
    if (typeof p.request === "function") return p;
    if (p.provider && typeof p.provider.request === "function") return p.provider;
    return null;
  };
  const isMetaMaskProvider = (p) => {
    if (!p) return false;
    if (p.isPhantom || p.isSolflare || p.isCoinbaseWallet || p.isRabby) return false;
    return !!(p.isMetaMask || p._metamask);
  };
  const getMetaMaskEvm = () => {
    const e = window.ethereum;
    if (!e) return null;
    if (Array.isArray(e.providers)) {
      return e.providers.find((p) => isMetaMaskProvider(p)) || null;
    }
    if (isMetaMaskProvider(e)) return e;
    return null;
  };
  /** Same MetaMask as ETH fund — EIP-6963 rdns, already-connected ETH provider, then window.ethereum. */
  const getMetaMaskProvider = () => {
    const fromEth = unwrapEip1193(ethWallet.eip1193) || unwrapEip1193(ethWallet.provider);
    if (fromEth && isMetaMaskProvider(fromEth)) return fromEth;
    for (const d of eip6963) {
      const rdns = String((d.info && d.info.rdns) || "");
      if (MM_RDNS[rdns] && d.provider) return d.provider;
    }
    const named = window.ethereum && window.ethereum.providers
      ? window.ethereum.providers.find((p) => p && (p.isMetaMaskProvider || (p.rdns && MM_RDNS[p.rdns])))
      : null;
    if (named) return named;
    return getMetaMaskEvm() || (window.metamask && typeof window.metamask.request === "function" ? window.metamask : null);
  };

  const isMmStd = (w) => {
    const n = String((w && w.name) || "").toLowerCase();
    return n === "metamask" || n.indexOf("metamask") >= 0;
  };
  const stdHasSolana = (w) => !!(w && w.features && (
    w.features["solana:signAndSendTransaction"] || w.features["solana:signTransaction"]
  ));
  const findMmStd = () => stdWallets.find(isMmStd) || null;
  const findMmStdSolana = () => stdWallets.find((w) => isMmStd(w) && stdHasSolana(w)) || findMmStd();

  const injectedMmSolana = () => {
    const mm = getMetaMaskProvider();
    const cands = [
      window.metamask && window.metamask.solana,
      mm && mm.solana,
      window.ethereum && window.ethereum.solana,
    ];
    const e = window.ethereum;
    if (e && Array.isArray(e.providers)) {
      const p = e.providers.find((x) => x && x.isMetaMask && (x.isSolana || x._isSolana || x.chain === "solana"));
      if (p) cands.push(p);
    }
    for (const p of cands) {
      if (p && (typeof p.connect === "function" || typeof p.request === "function"
          || typeof p.signAndSendTransaction === "function")) return p;
    }
    return null;
  };

  const getPhantom = () => {
    const p = (window.phantom && window.phantom.solana)
      || (window.solana && window.solana.isPhantom ? window.solana : null);
    return p || null;
  };
  const getSolflare = () => window.solflare || (window.solana && window.solana.isSolflare ? window.solana : null) || null;

  const unwrapSession = (raw) => {
    if (!raw) return { sessionScopes: {} };
    if (raw.sessionScopes || raw.session_scopes) return raw;
    if (raw.result && (raw.result.sessionScopes || raw.result.session_scopes)) return raw.result;
    return raw;
  };
  const sessionScopesOf = (session) => {
    const s = unwrapSession(session);
    return (s && (s.sessionScopes || s.session_scopes)) || {};
  };
  const solPkFromSession = (session) => {
    const scopes = sessionScopesOf(session);
    for (const [k, v] of Object.entries(scopes)) {
      if (String(k).indexOf("solana:") !== 0) continue;
      for (const a of (v && v.accounts) || []) {
        let pk = "";
        if (typeof a === "string") {
          const parts = a.split(":");
          pk = parts.length >= 3 ? parts[parts.length - 1] : a;
        } else if (a && typeof a === "object") {
          pk = toSolPk(a.address || a.publicKey || a.account || a);
        }
        if (isSolAddr(pk)) return pk;
      }
    }
    return "";
  };
  const caipCopyScopes = (session) => {
    const out = {};
    const scopes = sessionScopesOf(session);
    Object.keys(scopes).forEach((k) => {
      const sc = scopes[k] || {};
      out[k] = {
        methods: (sc.methods && sc.methods.length) ? sc.methods : (String(k).indexOf("eip155:") === 0 ? EIP155_CAIP_METHODS : []),
        notifications: sc.notifications || (String(k).indexOf("eip155:") === 0 ? ["eth_subscription"] : []),
      };
    });
    return out;
  };
  const buildOptionalScopes = (session, chainId) => {
    const optionalScopes = caipCopyScopes(session);
    const cid = Number(chainId) > 0 ? Number(chainId) : 1;
    const keys = ["eip155:1", "eip155:" + cid];
    keys.forEach((k) => {
      if (!optionalScopes[k]) {
        optionalScopes[k] = { methods: EIP155_CAIP_METHODS, notifications: ["eth_subscription"] };
      }
    });
    optionalScopes[SOL_MAINNET] = { methods: SOL_CAIP_METHODS.slice(), notifications: [] };
    return optionalScopes;
  };

  const caipCall = async (eth, method, params) => {
    const tries = [];
    if (params === undefined) {
      tries.push({ method });
      tries.push({ method, params: [] });
    } else {
      tries.push({ method, params });
      tries.push({ method, params: [params] });
    }
    let last = null;
    for (const arg of tries) {
      try {
        return await eth.request(arg);
      } catch (e) {
        if (isUserReject(e)) throw e;
        const c = rpcCode(e);
        if (c === 5100 || c === 4100 || c === 5302 || c === 5000) throw e;
        last = e;
      }
    }
    throw last || new Error(method + " failed");
  };

  const waitForSolPk = (eth, ms) => new Promise((resolve) => {
    let done = false;
    const finish = (pk) => { if (done) return; done = true; resolve(pk || ""); };
    const fromMsg = (payload) => {
      const pk = solPkFromSession(payload) || solPkFromSession(payload && payload.params);
      if (pk) finish(pk);
    };
    try {
      if (typeof eth.on === "function") {
        eth.on("wallet_sessionChanged", fromMsg);
        eth.on("message", (m) => {
          if (m && (m.method === "wallet_sessionChanged" || (m.params && m.params.sessionScopes))) fromMsg(m);
        });
      }
    } catch (_) { /* ignore */ }
    setTimeout(() => finish(""), ms);
  });

  const paintEthChip = () => {
    const btn = $("btn-connect");
    const chip = $("wallet-chip");
    if (!ethWallet.addr) {
      if (btn) { btn.textContent = "Connect MetaMask (ETH)"; btn.disabled = false; }
      if (chip) chip.classList.add("hidden");
      return;
    }
    if (btn) { btn.textContent = "Connected (ETH)"; btn.disabled = true; }
    if (chip) {
      chip.textContent = `ETH ${ethWallet.addr.slice(0, 6)}…${ethWallet.addr.slice(-4)}`;
      chip.classList.remove("hidden");
    }
  };

  const paintSolChip = () => {
    const pk = solWallet.pubkey;
    const label = solWallet.name || "SOL";
    const set = (btnId, chipId) => {
      const btn = $(btnId);
      const chip = $(chipId);
      if (!pk) {
        if (btn) {
          btn.textContent = btnId === "lp-btn-connect"
            ? "Connect MetaMask (SOL) / Phantom" : "Connect MetaMask (SOL)";
          btn.disabled = false;
        }
        if (chip) chip.classList.add("hidden");
        return;
      }
      if (btn) { btn.textContent = "Connected (SOL)"; btn.disabled = true; }
      if (chip) {
        chip.textContent = `${label} ${pk.slice(0, 4)}…${pk.slice(-4)}`;
        chip.classList.remove("hidden");
      }
    };
    set("sol-btn-connect", "sol-wallet-chip");
    set("lp-btn-connect", "lp-wallet-chip");
    const disc = $("lp-btn-disconnect");
    if (disc) disc.classList.toggle("hidden", !pk);
  };

  const pickSolAccount = (accs) => {
    for (const a of accs || []) {
      const chains = (a && a.chains) || [];
      if (chains.some((c) => String(c).indexOf("solana:") === 0)) return a;
      const pk = toSolPk(a && (a.address || a.publicKey));
      if (isSolAddr(pk)) return a;
    }
    return null;
  };

  const applySolWallet = (kind, label, pk, extra) => {
    const addr = isSolAddr(toSolPk(pk)) ? toSolPk(pk) : "";
    if (!addr) throw new Error("no Solana pubkey from " + label);
    solWallet.kind = kind;
    solWallet.name = label;
    solWallet.pubkey = addr;
    solWallet.provider = (extra && extra.provider) || null;
    solWallet.stdWallet = (extra && extra.stdWallet) || null;
    solWallet.stdAccount = (extra && extra.stdAccount) || null;
    solWallet.caipEth = (extra && extra.caipEth) || null;
    try { paintSolChip(); } catch (_) {}
    try {
      if (typeof postLpControl === "function") {
        Promise.resolve(postLpControl({ owner: addr })).then((r) => {
          if (r && r.lp && typeof updateLp === "function") updateLp({ sol: { lp: r.lp } });
        });
      }
    } catch (_) {}
  };

  const connectStdWallet = async (wallet, label) => {
    const feat = wallet.features || {};
    const connectFeat = feat["standard:connect"];
    if (!connectFeat || typeof connectFeat.connect !== "function") throw new Error("wallet has no connect");
    const res = await connectFeat.connect({ silent: false, chains: [SOL_MAINNET], chain: SOL_MAINNET });
    const accs = (res && res.accounts) || wallet.accounts || [];
    const acc = pickSolAccount(accs);
    if (!acc) throw new Error("no Solana account");
    applySolWallet("std", label, acc.address || acc.publicKey, { stdWallet: wallet, stdAccount: acc });
  };

  const connectInjectedSol = async (provider, label) => {
    let pk = provider.publicKey;
    try {
      if (typeof provider.connect === "function") {
        const res = await provider.connect({ onlyIfTrusted: false });
        pk = (res && res.publicKey) || provider.publicKey || pk;
      } else if (typeof provider.request === "function") {
        const accs = await provider.request({ method: "solana_requestAccounts" }).catch((e) => {
          if (isUserReject(e)) throw e;
          return null;
        });
        const found = (accs || []).map(toSolPk).find(isSolAddr);
        pk = found || pk;
      }
    } catch (e) {
      if (isUserReject(e)) throw rejectErr();
      throw e;
    }
    applySolWallet("injected", label, pk, { provider });
  };

  const requestSolSnap = async (eth) => {
    const ids = [SOL_SNAP_ID, "npm:solflare-wallet/solana-snap"];
    let last = null;
    for (const id of ids) {
      try {
        const params = {};
        params[id] = {};
        await eth.request({ method: "wallet_requestSnaps", params });
        return true;
      } catch (e) {
        if (isUserReject(e)) throw rejectErr();
        last = e;
      }
    }
    if (last && rpcCode(last) === -32601) return false;
    return false;
  };

  const connectCaipSolana = async (eth) => {
    if (!eth || typeof eth.request !== "function") return false;
    let chainId = 1;
    try {
      const hex = await eth.request({ method: "eth_chainId" });
      const n = parseInt(hex, 16);
      if (n > 0) chainId = n;
    } catch (_) { /* keep 1 */ }

    let session = null;
    try {
      session = unwrapSession(await caipCall(eth, "wallet_getSession"));
    } catch (e) {
      if (isUserReject(e)) throw rejectErr();
      session = { sessionScopes: {} };
    }
    let pk = solPkFromSession(session);
    if (pk) {
      applySolWallet("caip", "MetaMask", pk, { caipEth: eth });
      return true;
    }

    /* Empty session is not a fail — still prompt so the user can grant Solana. */
    const optionalScopes = buildOptionalScopes(session, chainId);
    const pendingPk = waitForSolPk(eth, 120000);
    const hasScopes = Object.keys(sessionScopesOf(session)).length > 0;
    try {
      if (hasScopes) {
        try {
          session = unwrapSession(await caipCall(eth, "wallet_updateSession", { optionalScopes }));
          pk = solPkFromSession(session);
        } catch (e) {
          if (isUserReject(e)) throw rejectErr();
        }
      }
      if (!pk) {
        try {
          session = unwrapSession(await caipCall(eth, "wallet_createSession", { optionalScopes }));
          pk = solPkFromSession(session);
        } catch (e) {
          if (isUserReject(e)) throw rejectErr();
          /* 5100 / -32601: Solana not enabled yet — caller will request snap and retry. */
        }
      }
    } catch (e) {
      if (isUserReject(e)) throw rejectErr();
      throw e;
    }
    if (!pk) {
      pk = await Promise.race([
        pendingPk,
        new Promise((r) => setTimeout(() => r(""), 1500)),
      ]) || pk;
    }
    if (!pk) {
      try {
        session = unwrapSession(await caipCall(eth, "wallet_getSession"));
        pk = solPkFromSession(session);
      } catch (_) { /* ignore */ }
    }
    if (!pk) return false;
    applySolWallet("caip", "MetaMask", pk, { caipEth: eth });
    return true;
  };

  const tryMmSolanaSurfaces = async (requireSolFeat) => {
    const mmStd = requireSolFeat
      ? stdWallets.find((w) => isMmStd(w) && stdHasSolana(w))
      : (findMmStdSolana() || findMmStd());
    if (mmStd) {
      try {
        await connectStdWallet(mmStd, "MetaMask");
        return true;
      } catch (e) {
        if (isUserReject(e)) throw rejectErr();
      }
    }
    const mmInj = injectedMmSolana();
    if (mmInj) {
      try {
        await connectInjectedSol(mmInj, "MetaMask");
        return true;
      } catch (e) {
        if (isUserReject(e)) throw rejectErr();
      }
    }
    return false;
  };

  const connectSolTab = async () => {
    const mm = getMetaMaskProvider();

    if (await tryMmSolanaSurfaces(true)) return;

    if (mm) {
      setSolFundStatus("requesting Solana permission in MetaMask…");
      try {
        if (await connectCaipSolana(mm)) return;
      } catch (e) {
        if (isUserReject(e)) throw rejectErr();
        /* keep going — still request snap so the popup can enable Solana */
      }

      if (await tryMmSolanaSurfaces(false)) return;

      setSolFundStatus("enabling Solana in MetaMask…");
      try {
        await requestSolSnap(mm);
      } catch (e) {
        if (isUserReject(e)) throw rejectErr();
      }
      await new Promise((r) => setTimeout(r, 400));
      try { window.dispatchEvent(new Event("wallet-standard:app-ready")); } catch (_) { /* ignore */ }
      try { window.dispatchEvent(new Event("eip6963:requestProvider")); } catch (_) { /* ignore */ }

      if (await tryMmSolanaSurfaces(false)) return;
      try {
        if (await connectCaipSolana(mm)) return;
      } catch (e) {
        if (isUserReject(e)) throw rejectErr();
      }
      throw noSolErr();
    }

    const phantom = getPhantom();
    const solflare = getSolflare();
    if (phantom) {
      await connectInjectedSol(phantom, "Phantom");
      return;
    }
    if (solflare) {
      await connectInjectedSol(solflare, "Solflare");
      return;
    }
    throw new Error("No Solana wallet (install MetaMask and enable Solana, or Phantom / Solflare)");
  };

  const solWeb3 = () => window.solanaWeb3 || null;

  const txToBase64 = (tx) => {
    const raw = tx.serialize({ requireAllSignatures: false, verifySignatures: false });
    let s = "";
    for (let i = 0; i < raw.length; i++) s += String.fromCharCode(raw[i]);
    return btoa(s);
  };

  const solLatestBlockhash = async () => {
    try {
      const r = await fetch("/api/sol/blockhash");
      const d = await r.json();
      if (d && d.blockhash) return d;
    } catch (_) { /* fall through */ }
    const w3 = solWeb3();
    if (!w3) throw new Error("solana web3.js not loaded");
    const urls = [
      (window.__lastState && window.__lastState.sol && window.__lastState.sol.rpc) || "",
      "https://api.mainnet-beta.solana.com",
      "https://solana-rpc.publicnode.com",
    ].filter(Boolean);
    let last = null;
    for (const url of urls) {
      try {
        const c = new w3.Connection(url, "confirmed");
        const bh = await c.getLatestBlockhash();
        return { blockhash: bh.blockhash, lastValidBlockHeight: bh.lastValidBlockHeight, rpc: url };
      } catch (e) { last = e; }
    }
    throw last || new Error("could not fetch blockhash");
  };

  const buildSolTransfer = (from, to, lamports, blockhash) => {
    const w3 = solWeb3();
    if (!w3) throw new Error("solana web3.js not loaded");
    const fromPk = new w3.PublicKey(from);
    const toPk = new w3.PublicKey(to);
    const tx = new w3.Transaction().add(w3.SystemProgram.transfer({
      fromPubkey: fromPk, toPubkey: toPk, lamports,
    }));
    tx.feePayer = fromPk;
    tx.recentBlockhash = blockhash;
    return tx;
  };

  const sigToBase58 = (sig) => {
    if (!sig) return "";
    if (typeof sig === "string") return sig.replace(/[^1-9A-HJ-NP-Za-km-z]/g, "");
    if (Array.isArray(sig) && sig.length) return sigToBase58(sig[0]);
    if (sig.signature) return sigToBase58(sig.signature);
    const w3 = solWeb3();
    if (w3 && sig instanceof Uint8Array) {
      try {
        if (w3.bs58 && typeof w3.bs58.encode === "function") return w3.bs58.encode(sig);
      } catch (_) { /* ignore */ }
    }
    return "";
  };

  const sendSolTransfer = async (tx) => {
    if (solWallet.kind === "injected" && solWallet.provider) {
      const p = solWallet.provider;
      if (typeof p.signAndSendTransaction === "function") {
        const out = await p.signAndSendTransaction(tx);
        return (out && (out.signature || out)) || "";
      }
      if (typeof p.request === "function") {
        const out = await p.request({ method: "signAndSendTransaction", params: { transaction: tx } });
        return (out && (out.signature || out)) || "";
      }
    }
    if (solWallet.kind === "std" && solWallet.stdWallet) {
      const feat = solWallet.stdWallet.features || {};
      const send = feat["solana:signAndSendTransaction"];
      const raw = tx.serialize({ requireAllSignatures: false, verifySignatures: false });
      if (send && send.signAndSendTransaction) {
        const out = await send.signAndSendTransaction({
          transaction: raw,
          account: solWallet.stdAccount,
          chain: SOL_MAINNET,
        });
        const sig = Array.isArray(out) ? out[0] : out;
        return (sig && (sig.signature || sig)) || "";
      }
      const sign = feat["solana:signTransaction"];
      if (sign && sign.signTransaction) {
        const signed = await sign.signTransaction({
          transaction: raw,
          account: solWallet.stdAccount,
          chain: SOL_MAINNET,
        });
        const bytes = (Array.isArray(signed) ? signed[0] : signed);
        const buf = bytes && (bytes.signedTransaction || bytes.transaction || bytes);
        const w3 = solWeb3();
        const rpc = (window.__lastState && window.__lastState.sol && window.__lastState.sol.rpc)
          || "https://api.mainnet-beta.solana.com";
        const c = new w3.Connection(rpc, "confirmed");
        return await c.sendRawTransaction(buf instanceof Uint8Array ? buf : new Uint8Array(buf));
      }
    }
    if (solWallet.kind === "caip" && solWallet.caipEth) {
      const b64 = txToBase64(tx);
      const methods = ["signAndSendTransaction", "solana_signAndSendTransaction"];
      let last = null;
      for (const method of methods) {
        try {
          const out = await solWallet.caipEth.request({
            method: "wallet_invokeMethod",
            params: {
              scope: SOL_MAINNET,
              request: {
                method,
                params: {
                  account: { address: solWallet.pubkey },
                  transaction: b64,
                  scope: SOL_MAINNET,
                },
              },
            },
          });
          if (typeof out === "string") return out;
          const sig = (out && (out.signature || out.hash || out.txid)) || "";
          if (sig) return sig;
        } catch (e) {
          last = e;
        }
      }
      throw last || new Error("CAIP signAndSendTransaction failed");
    }
    throw new Error("SOL wallet cannot sign (reconnect MetaMask Solana)");
  };

  const sendSolVersionedB64 = async (b64, extraSecretB64) => {
    if (!b64) throw new Error("no transaction");
    const w3 = solWeb3();
    const raw = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    let vtx = null;
    if (w3 && w3.VersionedTransaction) {
      vtx = w3.VersionedTransaction.deserialize(raw);
      if (extraSecretB64 && w3.Keypair) {
        const sec = Uint8Array.from(atob(extraSecretB64), (c) => c.charCodeAt(0));
        const kp = sec.length === 64 ? w3.Keypair.fromSecretKey(sec)
          : (sec.length === 32 && w3.Keypair.fromSeed ? w3.Keypair.fromSeed(sec) : null);
        if (kp) vtx.sign([kp]);
      }
    }
    if (solWallet.kind === "injected" && solWallet.provider) {
      const p = solWallet.provider;
      if (vtx && typeof p.signAndSendTransaction === "function") {
        const out = await p.signAndSendTransaction(vtx);
        return (out && (out.signature || out)) || "";
      }
      if (typeof p.request === "function") {
        let payload = b64;
        if (vtx) {
          const ser = vtx.serialize();
          let s = "";
          for (let i = 0; i < ser.length; i++) s += String.fromCharCode(ser[i]);
          payload = btoa(s);
        }
        const out = await p.request({
          method: "signAndSendTransaction",
          params: { transaction: payload },
        });
        return (out && (out.signature || out)) || "";
      }
    }
    if (solWallet.kind === "std" && solWallet.stdWallet) {
      const feat = solWallet.stdWallet.features || {};
      const send = feat["solana:signAndSendTransaction"];
      if (send && send.signAndSendTransaction) {
        const bytes = vtx ? vtx.serialize() : raw;
        const out = await send.signAndSendTransaction({
          transaction: bytes,
          account: solWallet.stdAccount,
          chain: SOL_MAINNET,
        });
        const sig = Array.isArray(out) ? out[0] : out;
        return (sig && (sig.signature || sig)) || "";
      }
    }
    if (solWallet.kind === "caip" && solWallet.caipEth) {
      let payload = b64;
      if (vtx) {
        const ser = vtx.serialize();
        let s = "";
        for (let i = 0; i < ser.length; i++) s += String.fromCharCode(ser[i]);
        payload = btoa(s);
      }
      const methods = ["signAndSendTransaction", "solana_signAndSendTransaction"];
      let last = null;
      for (const method of methods) {
        try {
          const out = await solWallet.caipEth.request({
            method: "wallet_invokeMethod",
            params: {
              scope: SOL_MAINNET,
              request: {
                method,
                params: {
                  account: { address: solWallet.pubkey },
                  transaction: payload,
                  scope: SOL_MAINNET,
                },
              },
            },
          });
          if (typeof out === "string") return out;
          const sig = (out && (out.signature || out.hash || out.txid)) || "";
          if (sig) return sig;
        } catch (e) { last = e; }
      }
      throw last || new Error("CAIP signAndSendTransaction failed");
    }
    throw new Error("SOL wallet cannot sign versioned tx");
  };

  const refreshSolFunds = async () => {
    const r = await postSolControl({ refresh_funds: true });
    if (r && r.funds && window.__lastState) {
      window.__lastState.sol = window.__lastState.sol || {};
      window.__lastState.sol.funds = r.funds;
      if (r.fund_guide) window.__lastState.sol.fund_guide = r.fund_guide;
      updateSolFunds(window.__lastState.sol);
    }
  };

  const solSponsorPk = () => {
    const s = (window.__lastState && window.__lastState.sol) || {};
    const funds = s.funds || {};
    const g = s.fund_guide || {};
    const w = s.wallets || {};
    return (funds.sponsor || {}).pubkey || g.sponsor || w.sponsor || "";
  };

  /* ETH tab: MetaMask EVM only — never writes __solWallet */
  const btnConnect = $("btn-connect");
  const chip = $("wallet-chip");
  if (btnConnect) {
    btnConnect.addEventListener("click", async () => {
      const mm = getMetaMaskProvider() || getMetaMaskEvm() || window.ethereum;
      if (!mm) {
        btnConnect.textContent = "No MetaMask";
        setEthFundStatus("install MetaMask for ETH", "err");
        return;
      }
      try {
        ethWallet.eip1193 = unwrapEip1193(mm) || mm;
        ethWallet.provider = new ethers.BrowserProvider(mm);
        await ethWallet.provider.send("eth_requestAccounts", []);
        const signer = await ethWallet.provider.getSigner();
        ethWallet.addr = await signer.getAddress();
        const net = await ethWallet.provider.getNetwork();
        const bal = await ethWallet.provider.getBalance(ethWallet.addr);
        if (chip) {
          chip.textContent = `ETH ${ethWallet.addr.slice(0, 6)}…${ethWallet.addr.slice(-4)} | chain ${net.chainId} | ${fmt.eth(Number(bal) / 1e18, 3)}`;
          chip.classList.remove("hidden");
        }
        btnConnect.textContent = "Connected (ETH)";
        btnConnect.disabled = true;
        setEthFundStatus("");
      } catch (e) {
        btnConnect.textContent = "Connect MetaMask (ETH)";
        setEthFundStatus("connect error / " + walletErr(e), "err");
        console.error(e);
      }
    });
  }

  const btnFund = $("btn-fund");
  if (btnFund) {
    btnFund.addEventListener("click", async () => {
      const amt = parseFloat($("fund-amt").value);
      const st = $("fund-status");
      if (!ethWallet.provider || !ethWallet.addr) {
        if (st) st.textContent = "connect MetaMask (ETH) first";
        return;
      }
      if (!amt || amt <= 0) { if (st) st.textContent = "enter an amount"; return; }
      try {
        if (st) { st.className = ""; st.textContent = "requesting signature..."; }
        const signer = await ethWallet.provider.getSigner();
        const tx = await signer.sendTransaction({ to: SPONSOR, value: ethers.parseEther(String(amt)) });
        if (st) st.textContent = `tx ${tx.hash.slice(0, 10)}… sent, waiting confirm`;
        await tx.wait();
        if (st) { st.className = "ok"; st.textContent = `confirmed: ${fmt.eth(amt)} to sponsor`; }
        $("fund-amt").value = "0.07";
      } catch (e) {
        if (st) { st.className = "err"; st.textContent = "cancelled / " + walletErr(e); }
      }
    });
  }

  /* SOL tab: MetaMask Solana first, Phantom/Solflare fallback — never writes __ethWallet */
  const solAmt = $("sol-fund-amt");
  if (solAmt && !solAmt.__dirtyBound) {
    solAmt.__dirtyBound = true;
    solAmt.addEventListener("input", () => { solAmt.dataset.dirty = "1"; });
  }
  const solBtnConnect = $("sol-btn-connect");
  if (solBtnConnect && !solBtnConnect.__bound) {
    solBtnConnect.__bound = true;
    solBtnConnect.addEventListener("click", async () => {
      try {
        setSolFundStatus("connecting Solana…");
        await connectSolTab();
        paintSolChip();
        setSolFundStatus(`connected ${solWallet.name} (SOL) — send uses this account`, "ok");
        if (solWallet.pubkey) postLpControl({ owner: solWallet.pubkey });
      } catch (e) {
        paintSolChip();
        setSolFundStatus(walletErr(e), "err");
      }
    });
  }
  const solBtnFund = $("sol-btn-fund");
  if (solBtnFund && !solBtnFund.__bound) {
    solBtnFund.__bound = true;
    solBtnFund.addEventListener("click", async () => {
      const amtEl = $("sol-fund-amt");
      const amt = parseFloat(amtEl && amtEl.value);
      const dest = solSponsorPk();
      if (!solWallet.pubkey) {
        try {
          setSolFundStatus("connecting Solana…");
          await connectSolTab();
          paintSolChip();
        } catch (e) {
          setSolFundStatus(walletErr(e), "err");
          return;
        }
      }
      if (!solWallet.pubkey) { setSolFundStatus("connect MetaMask (SOL) first", "err"); return; }
      if (!dest) { setSolFundStatus("sponsor pubkey missing — restart dashboard", "err"); return; }
      if (!amt || amt <= 0) { setSolFundStatus("enter an amount", "err"); return; }
      try {
        setSolFundStatus("requesting Solana signature…");
        const bh = await solLatestBlockhash();
        const lamports = Math.round(amt * 1e9);
        const tx = buildSolTransfer(solWallet.pubkey, dest, lamports, bh.blockhash);
        const sig = sigToBase58(await sendSolTransfer(tx));
        if (!sig) { setSolFundStatus("wallet returned no signature", "err"); return; }
        const short = sig.length > 12 ? sig.slice(0, 8) + "…" : sig;
        setSolFundStatus(
          `sent ${amt} SOL → sponsor · <a href="https://solscan.io/tx/${sig}" target="_blank" rel="noopener">${short}</a>`,
          "ok"
        );
        try { await refreshSolFunds(); } catch (_) { /* next WS tick */ }
      } catch (e) {
        setSolFundStatus("cancelled / " + walletErr(e), "err");
      }
    });
  }

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
  const btnKeep = $("btn-keep-live");
  const btnArm = $("btn-arm");
  const btnEdge = $("btn-edge");
  if (btnSim) btnSim.addEventListener("click", async () => {
    const cur = window.__lastBcast || {};
    await postControl({sim_only: !cur.sim_only});
  });
  if (btnKeep) btnKeep.addEventListener("click", async () => {
    const cur = window.__lastBcast || {};
    await postControl({keep_live: !cur.keep_live});
  });
  if (btnArm) btnArm.addEventListener("click", async () => {
    const cur = window.__lastBcast || {};
    if (cur.armed) await postControl({armed: false});
    else await postControl({armed: true, sim_only: false, keep_live: true});
  });
  if (btnEdge) btnEdge.addEventListener("click", async () => {
    const cur = window.__lastBcast || {};
    await postControl({edge_bias: !cur.edge_bias});
  });

  const solBtnSim = $("sol-btn-sim");
  const solBtnKeep = $("sol-btn-keep-live");
  const solBtnArm = $("sol-btn-arm");
  const solBtnEdge = $("sol-btn-edge");
  if (solBtnSim) solBtnSim.addEventListener("click", async () => {
    const cur = window.__lastSolBcast || {};
    await postSolControl({sim_only: !cur.sim_only});
  });
  if (solBtnKeep) solBtnKeep.addEventListener("click", async () => {
    const cur = window.__lastSolBcast || {};
    await postSolControl({keep_live: !cur.keep_live});
  });
  if (solBtnArm) solBtnArm.addEventListener("click", async () => {
    const cur = window.__lastSolBcast || {};
    if (cur.armed) await postSolControl({armed: false});
    else await postSolControl({armed: true, sim_only: false, keep_live: true});
  });
  if (solBtnEdge) solBtnEdge.addEventListener("click", async () => {
    const cur = window.__lastSolBcast || {};
    await postSolControl({edge_bias: !cur.edge_bias});
  });
})();
