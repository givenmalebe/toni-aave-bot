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
  const mkLiqVolumeChart = (id) => mkChart(id, {
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
          beginAtZero: true,
          ticks: { font: { size: 8 }, color: "#64748b", maxTicksLimit: 4 },
          grid: { color: "#1e293b55" },
        },
      },
    },
  });
  const liqVolumeChart = mkLiqVolumeChart("liq-volume-chart");
  const solLiqVolumeChart = mkLiqVolumeChart("sol-liq-volume-chart");
  const resizeLiqCharts = (prefix) => {
    const chart = prefix === "sol" ? solLiqVolumeChart : liqVolumeChart;
    if (chart) {
      try { chart.resize(); } catch (_) { /* panel hidden */ }
    }
  };

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
  let solMpFilter = "all", solOpFilter = "all", solOpProto = "all", solBcFilter = "all", solCpFilter = "all", solCpProto = "all";
  let solMpLiveCache = [], solOpCache = [], solBcRowsCache = [], solCpCache = [];
  let solWatchCache = [];
  let solOpLastMeta = {}, solCpLastMeta = {};
  const leftoverTxt = (o) => {
    if (!o) return "";
    if (Array.isArray(o.leftover)) return o.leftover.filter(Boolean).join(" · ");
    return String(o.leftover || "");
  };
  const GATE_TITLE_ETH = "Live send off until Sim off + Keep Live + LIQ_CONTRACT / GenericFlashLiquidator KIND. Hunt still works.";
  const GATE_TITLE_ETH_LIVE = "Aave sendable. Spark/Compound/Morpho stay blocked until GenericFlashLiquidator KIND. Hunt still works.";
  const GATE_TITLE_SOL = "Live send off until Sim off + Keep Live. Hunt still works.";
  const GATE_TITLE_SOL_LIVE = "Keep Live / armed — sendable when funded. Hunt still works.";
  const paintOpGate = (id, meta, chain) => {
    const el = $(id);
    if (!el) return;
    const gate = String((meta && meta.submit_gate) || "blocked");
    let label = "send off";
    let cls = "blocked";
    if (gate === "live") { label = "sendable"; cls = "live"; }
    else if (gate === "sim") { label = "sim only"; cls = "sim"; }
    const base = chain === "sol"
      ? (gate === "live" ? GATE_TITLE_SOL_LIVE : GATE_TITLE_SOL)
      : (gate === "live" ? GATE_TITLE_ETH_LIVE : GATE_TITLE_ETH);
    const reason = String((meta && meta.submit_reason) || "");
    el.textContent = label;
    el.className = "op-gate-chip " + cls;
    el.title = (reason && gate !== "live") ? `${base} (${reason})` : base;
  };
  const oppUsd = (row, key) => {
    if (!row) return null;
    const usd = row[key + "_usd"];
    if (usd != null && usd !== "") {
      const n = Number(usd);
      if (Number.isFinite(n)) return n;
    }
    const raw = Number(row[key]);
    if (!Number.isFinite(raw) || raw === 0) return null;
    return raw > 1e12 ? raw / 1e26 : raw;
  };
  const solHfUrgency = (hf) => {
    if (hf == null || hf >= 100) return { cls: "ok", label: "—" };
    if (hf < 1.0) return { cls: "crit", label: "liq" };
    if (hf < 1.05) return { cls: "hot", label: "hot" };
    if (hf < 1.1) return { cls: "warn", label: "warn" };
    if (hf < 1.25) return { cls: "ok", label: "ok" };
    return { cls: "ok", label: "safe" };
  };
  const solHfClass = (hf) => {
    if (hf == null) return "op-hf-ok";
    if (hf < 1.0) return "op-hf-crit";
    if (hf < 1.05) return "op-hf-hot";
    if (hf < 1.1) return "op-hf-warn";
    return "op-hf-ok";
  };
  const solShortPk = (pk) => {
    const s = pk || "";
    if (!s) return "--";
    return s.length > 12 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
  };
  const honestUsd = (v) => {
    if (v == null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  const fmtHonestUsd = (v) => {
    const n = honestUsd(v);
    return n == null ? "—" : fmt.usd(n);
  };
  const rowEstUsd = (c) => honestUsd(
    c && (c.est_profit_usd != null ? c.est_profit_usd : c.est));
  const rowNetUsd = (c) => honestUsd(
    c && (c.net_est_usd != null ? c.net_est_usd : c.net));
  const feedWindowNote = (shown, feedN, hourN, filtered) => {
    const feed = Number(feedN) || 0;
    const hour = hourN == null ? 0 : Number(hourN) || 0;
    if (filtered && shown !== feed)
      return `${shown}/${feed} shown · ${hour} in last hour`;
    return `${feed} in feed · ${hour} in last hour`;
  };
  const cpScanLine = (meta, feedN, lastHit) => {
    const m = meta || {};
    const windowN = m.n_logs != null ? Number(m.n_logs) : null;
    const feed = feedN != null ? Number(feedN) : Number(m.total) || 0;
    const hit = lastHit || m.last_hit_ts;
    const bits = [];
    if (windowN != null && Number.isFinite(windowN))
      bits.push(`this window <b>${fmt.num(windowN, 0)}</b>`);
    bits.push(`feed <b>${fmt.num(feed, 0)}</b>`);
    if (hit) bits.push(`last hit <b>${fmt.age(hit)} ago</b>`);
    return bits.join(" · ");
  };
  const cpLeftoverBits = (c) => [].concat(c && c.leftover || []).filter(Boolean);
  const HOUR_S = 3600;
  const SOL_SLOTS_PER_HOUR = 9000;
  const rowUnixTs = (c) => {
    let ts = Number(c && c.ts);
    if (!Number.isFinite(ts) || ts <= 0) return 0;
    if (ts > 1e12) ts = Math.floor(ts / 1000);
    return ts;
  };
  const inLastHour = (c, now, lastSlot) => {
    const ts = rowUnixTs(c);
    if (ts >= 1e9) return (now - ts) <= HOUR_S && (now - ts) >= -60;
    const slot = Number(c && c.slot);
    const tip = Number(lastSlot);
    if (Number.isFinite(slot) && slot > 0 && Number.isFinite(tip) && tip > 0)
      return (tip - slot) >= 0 && (tip - slot) <= SOL_SLOTS_PER_HOUR;
    return true;
  };
  const lastHitTs = (meta, rows) => {
    if (meta && meta.last_hit_ts) return meta.last_hit_ts;
    let mx = 0;
    for (const c of rows || []) {
      const ts = rowUnixTs(c);
      if (ts > mx) mx = ts;
    }
    return mx || null;
  };
  const pairMixFromRows = (rows, now, lastSlot) => {
    const hour = (rows || []).filter((c) => inLastHour(c, now, lastSlot));
    const src = hour.length ? hour : (rows || []);
    const pairCounts = {};
    for (const c of src) {
      const pair = c.pair || `${c.coll_sym || "?"}\u2192${c.debt_sym || "?"}`;
      pairCounts[pair] = (pairCounts[pair] || 0) + 1;
    }
    const total = Object.values(pairCounts).reduce((a, b) => a + b, 0) || 1;
    return Object.entries(pairCounts)
      .map(([pair, n]) => ({ pair, n, pct: Math.round((1000 * n) / total) / 10, share: n / total }))
      .sort((a, b) => b.n - a.n)
      .slice(0, 10);
  };
  const searcherShareFromRows = (rows, now, lastSlot) => {
    const hour = (rows || []).filter((c) => inLastHour(c, now, lastSlot));
    const by = {};
    for (const c of hour) {
      const addr = String(c.searcher || c.liquidator || "").trim();
      if (!addr) continue;
      const slot = by[addr] || {
        addr, searcher: addr, short: addr.slice(0, 10), n: 0, est: 0, sum_est: 0,
      };
      slot.n += 1;
      const est = rowEstUsd(c);
      if (est != null) {
        slot.est += est;
        slot.sum_est += est;
      }
      by[addr] = slot;
    }
    const n = hour.length;
    return Object.values(by)
      .map((v) => ({
        ...v,
        est: Math.round(v.est * 100) / 100,
        sum_est: Math.round(v.sum_est * 100) / 100,
        share: n ? v.n / n : 0,
        pct: n ? Math.round((1000 * v.n) / n) / 10 : 0,
      }))
      .sort((a, b) => b.n - a.n || b.est - a.est)
      .slice(0, 10);
  };
  const searcherShareAllTime = (rows) => {
    const by = {};
    for (const c of (rows || [])) {
      const addr = String(c.searcher || c.liquidator || "").trim();
      if (!addr) continue;
      const slot = by[addr] || {
        addr, searcher: addr, short: addr.slice(0, 10), n: 0, est: 0, sum_est: 0,
      };
      slot.n += 1;
      const est = rowEstUsd(c);
      if (est != null) {
        slot.est += est;
        slot.sum_est += est;
      }
      by[addr] = slot;
    }
    const n = (rows || []).length;
    return Object.values(by)
      .map((v) => ({
        ...v,
        est: Math.round(v.est * 100) / 100,
        sum_est: Math.round(v.sum_est * 100) / 100,
        share: n ? v.n / n : 0,
        pct: n ? Math.round((1000 * v.n) / n) / 10 : 0,
      }))
      .sort((a, b) => b.n - a.n || b.est - a.est)
      .slice(0, 10);
  };
  const resolveTopSearchers = (meta, rows, lastSlot) => {
    const now = Math.floor(Date.now() / 1000);
    const computed = searcherShareFromRows(rows, now, lastSlot);
    if (computed.length) return { tops: computed, label: "1h" };
    const allTime = searcherShareAllTime(rows);
    if (allTime.length) return { tops: allTime, label: "all time" };
    const metaTops = (meta && meta.top_searchers) || [];
    return { tops: metaTops, label: metaTops.length ? "1h" : "" };
  };
  const renderTopSearchers = ({ tableId, emptyId, tops, meta, shortFn, label }) => {
    const body = $(tableId) && $(tableId).querySelector("tbody");
    const empty = $(emptyId);
    const scanned = !!(meta && (
      meta.last_scan || meta.last_slot || meta.last_block || meta.status === "ok"
      || (meta.total != null && Number(meta.total) > 0)
    ));
    if (empty) {
      if (tops.length) {
        empty.style.display = "none";
      } else {
        empty.style.display = "block";
        empty.classList.remove("err");
        empty.textContent = scanned
          ? "no searcher share yet"
          : "waiting for liquidations\u2026";
      }
    }
    if (!body) return;
    body.innerHTML = tops.map((t, i) => {
      const addr = t.addr || t.searcher || "";
      const pct = t.pct != null ? Number(t.pct) : Math.round((Number(t.share) || 0) * 100);
      const est = t.est != null ? t.est : t.sum_est;
      const lbl = shortFn ? shortFn(addr) : (addr ? `${addr.slice(0, 6)}\u2026${addr.slice(-4)}` : "--");
      return `<tr>
        <td class="dim">${i + 1}</td>
        <td class="mono copy" title="${addr}" data-addr="${addr}">${lbl}</td>
        <td><div class="cp-bar-track"><div class="cp-bar" style="width:${Math.min(100, Math.max(0, pct || 0))}%"></div></div></td>
        <td>${fmt.num(t.n, 0)}</td>
        <td style="color:var(--amber)">${fmtHonestUsd(est)}</td>
      </tr>`;
    }).join("");
    const tagEl = body.closest(".cp-col") && body.closest(".cp-col").querySelector("h3 .tag");
    if (tagEl && label) tagEl.textContent = label + " share";
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
    if (o.presigned) {
      const pt = o.presigned_proto ? o.presigned_proto + " " : "";
      const age = o.presigned_age_s != null ? ` (${o.presigned_age_s}s)` : "";
      bits.push(`<span class="pill ok" title="pre-signed bundle ready for ${pt.trim()}${age}">${pt}PRESIGNED</span>`);
    }
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
      else if (ready.liq) bcastEl.textContent = "liq";
      else bcastEl.textContent = "blocked";
      bcastEl.style.color = (!bc.enabled ? "var(--amber)"
        : ready.liq ? "var(--green)" : "var(--red)");
    }
    const race = sol.race || {};
    const raceEl = $("p-race");
    if (raceEl) {
      if (race.mode) {
        const spent = race.tuition_spent_sol || 0, cap = race.tuition_day_sol || 0;
        const tm = race.tip_mult || {};
        const c = tm.contested != null ? tm.contested : 1;
        raceEl.textContent = `RACE ${spent.toFixed(4)}/${cap} SOL mult ${c.toFixed(2)}`;
        raceEl.style.color = spent >= cap * 0.9 ? "var(--amber)" : "var(--green)";
      } else raceEl.textContent = "race off";
    }
    renderFeedPills(s.feeds);
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
        intel: "SOL Learning / Intel", broadcast: "SOL Broadcast",
      },
      roles: {
        mempool: "landing", prices: "oracle", funds: "wallets", sweep: "HF",
        competitors: "liq", intel: "learn", broadcast: "submit",
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
    const totalSol = ((funds.sponsor || {}).sol || 0) + ((funds.bot || {}).sol || 0);
    const totalShort = ((funds.sponsor || {}).shortfall_sol || 0) + ((funds.bot || {}).shortfall_sol || 0);
    const verdText = totalShort > 0
      ? `${fmt.num(totalSol, 4)} SOL funded · ${fmt.num(totalShort, 2)} short`
      : (perf.verdict || "—");
    set("sol-fp-verdict", verdText);
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
      const sponsorOk = ((funds.sponsor || {}).sol || 0) >= ts;
      const botOk = ((funds.bot || {}).sol || 0) >= tb;
      const row = (label, amt, pk, note, ok) => {
        const short = pk ? `${pk.slice(0, 4)}…${pk.slice(-4)}` : "unset";
        const icon = ok ? "✓" : "○";
        return `<div class="sol-fund-row"><span class="dim">${icon} ${label}</span><b>${amt}</b>` +
          (pk
            ? `<span class="mono copy" data-addr="${pk}" title="${pk}">${short}</span>`
            : `<span class="mono dim">unset</span>`) +
          `<span class="dim">${note}</span></div>`;
      };
      check.innerHTML =
        `<div class="sol-fund-h">from funder <span class="mono copy" data-addr="${fd}">${fd ? fd.slice(0,4)+"…"+fd.slice(-4) : "—"}</span></div>` +
        row("sponsor", ts + " SOL", sp, "Jito + prio", sponsorOk) +
        row("bot", tb + " SOL", bt, "CU + inventory", botOk);
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
    if (note) note.textContent = `${live.length}/${solMpLiveCache.length} · decoded`;
    if (empty) empty.style.display = live.length ? "none" : "block";
    body.innerHTML = live.slice(0, 50).map((t) => {
      const slot = t.slot != null ? String(t.slot) : "--";
      const kind = t.kind || t.flags || t.cls || "?";
                    const link = t.solscan
        ? `<a href="${t.solscan}" target="_blank" rel="noopener" style="color:var(--violet)">${(t.sig || t.tx || slot).toString().slice(0, 10)}</a>` +
          (t.sig || t.tx ? ` <span class="mono copy op-link" data-addr="${t.sig || t.tx}" title="${t.sig || t.tx}">copy</span>` : "")
        : `<span class="mono dim">${slot.slice(-8)}</span>`;
      const pair = t.pair || t.searcher || "";
      const feeVal = t.fee != null ? t.fee : (t.fee_lamports != null ? Math.round(t.fee_lamports / 1_000_000_000 * 1e6) : null);
      const fee = feeVal != null ? fmtUl(feeVal) : (t.hf != null ? fmt.num(t.hf, 3) : "—");
      const vsMed = t.vs_med != null ? fmt.num(t.vs_med, 1) + "×"
        : (t.profit_usd != null ? fmt.usd(t.profit_usd, 3) : "—");
      return `<tr>
        <td><span class="sol-kind ${kind}">${kind}</span></td>
        <td class="mono" title="${slot}">${slot}</td>
        <td>${pair}</td>
        <td>${fee}</td>
        <td class="${Number(t.vs_med) > 2 ? "green" : "dim"}">${vsMed}</td>
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
    const nvTps = meta.nv_tps != null ? Math.round(meta.nv_tps) : (meta.tps != null ? Math.round(meta.tps) : null);
    set("sol-mp-mev-share", nvTps != null ? fmt.num(nvTps, 0) : "--", "dim");
    const badge = $("sol-mp-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "mp-pressure-badge " + (meta.pressure || "idle");
    }
    const metaEl = $("sol-mp-meta");
    if (metaEl) {
      metaEl.innerHTML =
        (meta.scanned != null ? `<span>decoded <b>${fmt.num(meta.decoded, 0)}</b></span>` : "") +
        (meta.refresh_n != null ? `<span>refresh <b>${fmt.num(meta.refresh_n, 0)}</b></span>` : "") +
        (meta.jito_bundles != null ? `<span>jito <b>${fmt.num(meta.jito_bundles, 0)}</b></span>` : "") +
        (meta.contested != null ? `<span>race <b>${fmt.num(meta.contested, 0)}</b></span>` : "") +
        (meta.zero_pct != null ? `<span>zero <b>${fmt.num(meta.zero_pct, 0)}%</b></span>` : "") +
        `<span class="dim">${meta.landing_note || "Solend landing · priority fees"}</span>`;
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
        ? `<span>cluster <b>${fmt.num(meta.tps, 0)}</b> TPS</span>` +
          (meta.nv_tps != null ? `<span>non-vote <b>${fmt.num(meta.nv_tps, 0)}</b></span>` : "") +
          `<span>hot <b>${fmt.num(meta.hot_share_pct, 0)}%</b></span>` +
          (meta.zero_pct != null ? `<span>zero-fee <b>${fmt.num(meta.zero_pct, 0)}%</b></span>` : "")
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

  const solProtoId = (o) => {
    const p = String((o && (o.protocol_id || o.protocol)) || "").toLowerCase();
    if (!p || p === "solend" || p === "save") return "solend";
    if (p.indexOf("kamino") >= 0 || p === "klend") return "kamino";
    if (p.indexOf("marginfi") >= 0 || p === "mfi") return "marginfi";
    if (p.indexOf("drift") >= 0) return "drift";
    return p || "solend";
  };
  const solProtoLabel = (o) => {
    const map = { solend: "Solend", kamino: "Kamino", marginfi: "MarginFi", drift: "Drift" };
    return map[solProtoId(o)] || solProtoId(o);
  };
  const solProtoPill = (o) => {
    const id = solProtoId(o);
    return `<span class="pill proto proto-${id}">${solProtoLabel(o)}</span>`;
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
    if (solOpProto !== "all") rows = rows.filter((o) => solProtoId(o) === solOpProto);
    const note = $("sol-op-feed-note");
    const watchN = (solOpLastMeta && solOpLastMeta.watch_n) != null
      ? solOpLastMeta.watch_n
      : solWatchCache.length;
    if (note) {
      const protoTag = solOpProto !== "all" ? ` · ${solProtoLabel({protocol_id: solOpProto})}` : "";
      note.textContent = solOpCache.length
        ? `${rows.length}/${solOpCache.length} in feed · HF<1 +EV${protoTag}`
        : `0 in feed · ${fmt.num(watchN, 0)} in watch`;
    }
    if (empty) {
      if (rows.length) {
        empty.style.display = "none";
        empty.classList.remove("err");
      } else {
        empty.style.display = "block";
        const m = solOpLastMeta || {};
        const fatal = m.status === "error" && !(m.last_scan && (m.scanned || m.obligation_hydrated || m.obligation_probed));
        if (fatal || (m.status === "error" && !m.last_scan)) {
          empty.classList.add("err");
          empty.textContent = "sweep error: " + (m.note || m.status);
        } else if (solOpFilter !== "all" || solOpProto !== "all") {
          empty.classList.remove("err");
          empty.textContent = `no HF<1 +EV match this filter · ${solOpProto !== "all" ? solProtoLabel({protocol_id: solOpProto}) : solOpFilter} · ${solOpCache.length} in feed · ${fmt.num(watchN, 0)} in watch`;
        } else if (m.last_scan || m.last_slot) {
          empty.classList.remove("err");
          empty.textContent = `no HF<1 +EV this sweep · watch ${fmt.num(watchN, 0)}`
            + (m.last_scan ? ` · last scan ${fmt.age(m.last_scan)} ago` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "scanning Solend + Kamino + MarginFi + Drift for HF<1 +EV…";
        }
      }
    }
    body.innerHTML = rows.slice(0, 80).map((o) => {
      const user = o.obligation || o.user || "";
      const short = solShortPk(user);
      const hf = o.hf;
      const hfCell = hf == null ? "--" : (hf >= 100 ? "∞" : Number(hf).toFixed(3));
      const pair = `${o.coll_sym || o.collateral_sym || "?"} → ${o.debt_sym || "?"}`;
      const repay = o.repay_usd != null ? o.repay_usd : o.cover_usd;
      const sizes = (o.coll_usd != null || o.debt_usd != null)
        ? `<div class="dim">${fmt.usd(o.coll_usd)} / ${fmt.usd(o.debt_usd)}${repay != null ? ` · repay ${fmt.usd(repay)}` : ""}</div>` : "";
      const net = o.net_usd != null ? o.net_usd : o.profit_usd;
      const netColor = net == null ? "var(--dim)" : net > 0 ? "var(--green)" : "var(--red)";
      return `<tr>
        <td class="mono copy" data-addr="${user}" title="${user}">${short}</td>
        <td>${solProtoPill(o)}</td>
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
    const wl = (sol.watchlist || []).filter((w) => !w.proxy && w.hf != null && w.hf >= 1.0).slice(0, 50);
    const set = (id, v, cls) => {
      const e = $(id); if (!e) return;
      e.textContent = v;
      if (cls) e.className = "big " + cls;
    };
    const count = meta.count != null ? meta.count : opps.length;
    set("sol-op-count", fmt.num(count, 0));
    set("sol-op-best", meta.best_profit ? fmt.usd(meta.best_profit) : "—", "green");
    set("sol-op-edge-n", fmt.num(meta.edge_n, 0), "amber");
    set("sol-op-sweep", fmt.num(meta.watch_n != null ? meta.watch_n : wl.length, 0), "dim");
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
    paintOpGate("sol-op-gate", meta, "sol");
    const metaEl = $("sol-op-meta");
    if (metaEl) {
      const sweepBot = (sol.bots || {}).sweep || {};
      metaEl.innerHTML =
        (meta.scanned != null ? `<span>scanned <b>${fmt.num(meta.scanned, 0)}</b></span>` : "") +
        (meta.obligation_hydrated != null ? `<span>hyd <b>${fmt.num(meta.obligation_hydrated, 0)}</b></span>` : "") +
        (sweepBot.status ? `<span>sweep <b>${sweepBot.status}</b></span>` : "") +
        (meta.last_slot ? `<span>slot <b>${meta.last_slot}</b></span>` : "") +
        (meta.last_scan ? `<span>${fmt.age(meta.last_scan)} ago</span>` : "");
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
        : `<span class="dim">no liquidatable pairs · see watch →</span>`;
    }
    const leftoverEl = $("sol-op-leftover");
    if (leftoverEl) {
      const bits = (meta.leftovers || []).filter(Boolean);
      if (bits.length) {
        leftoverEl.style.display = "";
        leftoverEl.textContent = "leftover · " + bits.slice(0, 4).join(" · ");
      } else if (meta.note && !opps.length && /GPA|blocked|SOLANA_RPC/i.test(String(meta.note))) {
        leftoverEl.style.display = "";
        leftoverEl.textContent = "leftover · " + String(meta.note).slice(0, 180);
      } else {
        leftoverEl.style.display = "none";
        leftoverEl.textContent = "";
      }
    }
    solOpCache = opps.slice(0, 80);
    solWatchCache = wl;
    renderSolOpps();
    const wnote = $("sol-watch-note");
    if (wnote) wnote.textContent = `${Math.min(wl.length, 50)}/50 · up to 50 lowest HF`;
    const wempty = $("sol-watch-empty");
    if (wempty) {
      if (wl.length) wempty.style.display = "none";
      else {
        wempty.style.display = "block";
        wempty.textContent = meta.last_scan
          ? "no closest HF this sweep"
          : "waiting for lowest-HF hydrates…";
      }
    }
    const wbody = $("sol-watch-table") && $("sol-watch-table").querySelector("tbody");
    if (wbody) {
      const wlFilt = solOpProto !== "all" ? wl.filter((w) => solProtoId(w) === solOpProto) : wl;
      wbody.innerHTML = wlFilt.slice(0, 50).map((w) => {
        const hf = w.hf == null ? null : Number(w.hf);
        const hfCell = hf == null || !Number.isFinite(hf) ? "—" : (hf >= 100 ? "∞" : hf.toFixed(3));
        const urg = solHfUrgency(hf);
        const user = w.user || w.obligation || "";
        const left = leftoverTxt(w);
        const urgCell = left
          ? `<span class="op-urg ${urg.cls}">${urg.label}</span> <span class="pill blocked" title="${String(left).replace(/"/g, "'")}">leftover</span>`
          : `<span class="op-urg ${urg.cls}">${urg.label}</span>`;
        return `<tr>
          <td class="mono copy" data-addr="${user}" title="click to copy">${solShortPk(user)}</td>
          <td>${solProtoPill(w)}</td>
          <td class="${solHfClass(hf)}">${hfCell}</td>
          <td>${w.coll_usd != null ? fmt.usd(w.coll_usd) : "—"}</td>
          <td>${w.debt_usd != null ? fmt.usd(w.debt_usd) : "—"}</td>
          <td>${urgCell}</td>
        </tr>`;
      }).join("");
    }
  };

  const renderSolComps = () => {
    const body = $("sol-comp-table") && $("sol-comp-table").querySelector("tbody");
    const empty = $("sol-comp-empty");
    if (!body) return;
    let rows = solCpCache;
    if (solCpFilter === "miss") rows = rows.filter((r) => r.missed || r.missed_by_us);
    else if (solCpFilter === "edge") rows = rows.filter((r) => r.edge);
    else if (solCpFilter === "profit") rows = rows.filter((r) => rowEstUsd(r) != null || rowNetUsd(r) != null);
    else if (solCpFilter === "revert") rows = rows.filter((r) => /revert/i.test(r.flags || ""));
    if (solCpProto !== "all") rows = rows.filter((r) => solProtoId(r) === solCpProto);
    const m = solCpLastMeta || {};
    const note = $("sol-cp-feed-note");
    if (note) note.textContent = feedWindowNote(
      rows.length, solCpCache.length, m.count_1h, solCpFilter !== "all");
    if (empty) {
      if (rows.length) {
        empty.style.display = "none";
        empty.classList.remove("err");
      } else {
        empty.style.display = "block";
        const st = String(m.status || "");
        const isErr = st.startsWith("err") || st === "error" || (m.errors && m.errors.length && !m.last_scan);
        if (isErr && st && st !== "ok") {
          empty.classList.add("err");
          empty.textContent = "scan error: " + (m.error || st.replace(/^err\s*/i, "") || "RPC failed");
        } else if (solCpFilter !== "all" || solCpProto !== "all") {
          empty.classList.remove("err");
          empty.textContent = `no rows match filter ${solCpProto !== "all" ? solProtoLabel({protocol_id: solCpProto}) : solCpFilter} · ${solCpCache.length} in feed · ${fmt.num(m.count_1h, 0)} in last hour`;
        } else if (m.last_scan || m.last_slot) {
          empty.classList.remove("err");
          empty.textContent = `no confirmed liquidations in feed yet`
            + (m.n_logs != null ? ` · this window ${fmt.num(m.n_logs, 0)}` : "")
            + (m.scanned != null ? ` · ${fmt.num(m.scanned, 0)} sigs` : "")
            + (m.last_slot ? ` · slot ${m.last_slot}` : "")
            + (m.last_scan ? ` · ${fmt.age(m.last_scan)} ago` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "scanning Solend + Kamino + MarginFi + Drift liquidation signatures…";
        }
      }
    }
    body.innerHTML = rows.slice(0, 50).map((c) => {
      const searcher = c.searcher || "";
      const user = c.user || "";
      const sig = c.sig || c.tx || "";
      const est = rowEstUsd(c);
      const net = rowNetUsd(c);
      const flags = [];
      if (c.missed || c.missed_by_us)
        flags.push(`<span class="cp-flag miss" title="we saw this confirmed liq and did not send">miss</span>`);
      if (c.edge)
        flags.push(`<span class="cp-flag edge" title="long-tail pair">edge</span>`);
      if (/revert/i.test(c.flags || "")) flags.push(`<span class="cp-flag revert">revert</span>`);
      const left = cpLeftoverBits(c);
      if (left.length)
        flags.push(`<span class="cp-flag left" title="${left.join(", ")}">leftover</span>`);
      const tx = sig
        ? `<a href="${c.solscan || ("https://solscan.io/tx/" + sig)}" target="_blank" rel="noopener" style="color:var(--cyan)">${sig.slice(0, 8)}…</a>` +
          ` <span class="mono copy op-link" data-addr="${sig}" title="${sig}">copy</span>`
        : `<span class="dim">--</span>`;
      return `<tr>
        <td class="dim" title="slot ${c.slot || "?"}">${c.ts ? fmt.age(c.ts) : (c.slot || "--")}</td>
        <td>${solProtoPill(c)}</td>
        <td><b>${c.pair || "liq"}</b></td>
        <td class="mono copy" data-addr="${searcher}" title="${searcher}">${solShortPk(searcher)}</td>
        <td class="mono copy dim" data-addr="${user}" title="${user}">${solShortPk(user)}</td>
        <td class="dim" title="on-chain fee × SOL price — not their profit">${c.gas_usd != null ? fmt.usd(c.gas_usd) : "—"}</td>
        <td style="color:${est != null ? "var(--amber)" : "var(--dim)"}">${fmtHonestUsd(est)}</td>
        <td style="color:${net != null ? "var(--green)" : "var(--dim)"}"><b>${fmtHonestUsd(net)}</b></td>
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
    const solFeedN = (sol.competitors || []).length || meta.total || 0;
    const solH1 = meta.count_1h || 0;
    set("sol-cp-count", fmt.num(solH1, 0));
    set("sol-cp-count-feed", solH1 === 0 && solFeedN ? `${fmt.num(solFeedN, 0)} in feed` : "");
    set("sol-cp-searchers", fmt.num(meta.unique_searchers, 0), "dim");
    const solAllS = (sol.competitors || []).length ? [...new Set((sol.competitors || []).map(c => c.searcher || c.liquidator || "").filter(Boolean))].length : 0;
    set("sol-cp-searchers-feed", !(meta.unique_searchers) && solAllS ? `${solAllS} all time` : "");
    set("sol-cp-sum-est", honestUsd(meta.sum_est_profit) != null ? fmt.usd(meta.sum_est_profit) : "\u2014", "amber");
    set("sol-cp-missed",
      meta.missed_by_us
        ? `${fmt.num(meta.missed_by_us, 0)}${meta.miss_rate_pct ? ` \u00b7 ${fmt.num(meta.miss_rate_pct, 0)}%` : ""}`
        : "0", "red");
    const badge = $("sol-cp-pressure");
    if (badge) {
      badge.textContent = meta.pressure || "idle";
      badge.className = "cp-pressure-badge " + (meta.pressure || "idle");
    }
    const metaEl = $("sol-cp-meta");
    if (metaEl) {
      const hit = lastHitTs(meta, sol.competitors || []);
      metaEl.innerHTML =
        `<span>${cpScanLine(meta, (sol.competitors || []).length, hit)}</span>` +
        `<span>edge <b>${fmt.num(meta.edge_n, 0)}</b></span>` +
        `<span>reverts <b>${fmt.num(meta.revert_n, 0)}</b></span>` +
        (meta.scanned != null ? `<span>scanned <b>${fmt.num(meta.scanned, 0)}</b></span>` : "") +
        `<span>tracked <b>${fmt.num(meta.total, 0)}</b></span>` +
        (meta.last_slot ? `<span>slot <b>${meta.last_slot}</b></span>` : "") +
        (meta.last_scan ? `<span>${fmt.age(meta.last_scan)} ago</span>` : "") +
        (meta.status && meta.status !== "ok" ? `<span style="color:var(--red)">${meta.status}</span>` : "");
    }
    const leftEl = $("sol-cp-leftover");
    if (leftEl) {
      const bits = (meta.leftovers || []).filter(Boolean);
      if (bits.length) {
        leftEl.style.display = "";
        leftEl.textContent = "leftover · " + bits.slice(0, 4).join(" · ");
      } else {
        leftEl.style.display = "none";
        leftEl.textContent = "";
      }
    }
    solCpCache = sol.competitors || [];
    renderSolComps();
    const { tops: solTops, label: solTopLabel } = resolveTopSearchers(meta, solCpCache, meta.last_slot);
    if (solTops.length && !(meta.unique_searchers))
      set("sol-cp-searchers", fmt.num(solTops.length, 0), "dim");
    renderTopSearchers({
      tableId: "sol-cp-searcher-table",
      emptyId: "sol-cp-searcher-empty",
      tops: solTops,
      meta,
      shortFn: solShortPk,
      label: solTopLabel,
    });
    const pbody = $("sol-cp-pair-table") && $("sol-cp-pair-table").querySelector("tbody");
    const pnote = $("sol-cp-pair-note");
    const ptags = $("sol-cp-pair-tag");
    const plabel = $("sol-cp-mix-label");
    const mix1h = meta.pair_mix || [];
    const solNow = Math.floor(Date.now() / 1000);
    const mix = mix1h.length ? mix1h : pairMixFromRows(solCpCache, solNow, meta.last_slot);
    const solMixLabel = mix1h.length ? "last hour" : "all time";
    if (pnote) pnote.textContent = String(mix.length);
    if (ptags) ptags.textContent = solMixLabel;
    if (plabel) plabel.textContent = `pair mix (${solMixLabel})`;
    if (pbody) {
      pbody.innerHTML = mix.map((p) =>
        `<tr><td>${p.pair}</td>
          <td><div class="cp-bar-track"><div class="cp-bar pair" style="width:${Math.min(100, p.pct || 0)}%"></div></div></td>
          <td>${p.n}</td><td class="dim">${p.pct ?? ""}%</td></tr>`
      ).join("") || `<tr><td colspan="4" class="dim">no confirmed liquidations</td></tr>`;
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
      || `<span class="dim">no confirmed liquidations</span>`;
    const ch = (hist && hist.sol_comp_1h) || [];
    if (solChartComp && ch.length) {
      solChartComp.data.labels = ch.map((_, i) => i);
      solChartComp.data.datasets[0].data = ch;
      solChartComp.data.datasets[1].data = ch.map(() => 0);
      solChartComp.update("none");
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
    const solModeLabel = bc.armed ? (bc.keep_live ? "keep live" : "armed") : bc.sim_only ? "sim" : bc.keep_live ? "keep live" : "off";
    setTxt("sol-bc-mode", solModeLabel, bc.armed ? "green" : (bc.sim_only ? "" : "dim"));
    setTxt("sol-bc-dyn-liq", bc.dyn_min_liq != null ? "$" + fmt.num(bc.dyn_min_liq) : "--", "amber");
    const lastStage = sum.last_stage || "--";
    const okStage = ["sent", "ok", "simulated"].includes(String(lastStage).toLowerCase());
    setTxt("sol-bc-last-stage", lastStage, okStage ? "green" : (lastStage === "--" ? "dim" : "amber"));
    const st = $("sol-bcast-status");
    if (st) {
      st.innerHTML =
        `<span>sim <b>${bc.sim_only ? "ON" : "off"}</b></span>` +
        `<span>keep live <b>${bc.keep_live ? "ON" : "off"}</b></span>` +
        `<span>armed <b style="color:${bc.armed ? "var(--green)" : "var(--dim)"}">${bc.arm_note || (bc.armed ? "LIVE" : "no")}</b></span>` +
        `<span>edge <b>${bc.edge_bias ? "ON" : "off"}</b></span>` +
        `<span>gates <b style="color:${ready.liq ? "var(--green)" : "var(--red)"}">${ready.liq ? "clear" : "blocked"}</b></span>` +
        `<span>hist <b>${fmt.num(sum.n_hist || 0, 0)}</b></span>` +
        (bc.liq_contract ? `<span>liq <b>${String(bc.liq_contract).slice(0, 10)}\u2026</b></span>` : "");
    }
    const pills = $("sol-bc-mode-pills");
    if (pills) {
      pills.innerHTML =
        `<span class="bc-pill ${bc.sim_only ? "on" : ""}">sim ${bc.sim_only ? "ON" : "off"}</span>` +
        `<span class="bc-pill ${bc.keep_live ? "keep" : ""}">keep live ${bc.keep_live ? "auto-renew" : "off"}</span>` +
        `<span class="bc-pill ${bc.armed ? "live" : ""}">${bc.arm_note || (bc.armed ? "armed LIVE" : "not armed")}</span>` +
        `<span class="bc-pill ${bc.edge_bias ? "on" : ""}">edge ${bc.edge_bias ? "on" : "off"}</span>` +
        `<span class="bc-pill ${ready.liq ? "on" : "warn"}">liq ${ready.liq ? "ready" : "blocked"}</span>`;
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
    const nearBody = $("sol-bc-near-table") && $("sol-bc-near-table").querySelector("tbody");
    if (nearBody || $("sol-bc-near-empty")) {
      const nearEmpty = $("sol-bc-near-empty");
      const nearNote = $("sol-bc-near-note");
      const hints = bc.near_miss_hints || [];
      if (nearNote) nearNote.textContent = hints.length ? `${hints.length} pairs` : "learning";
      if (nearEmpty) nearEmpty.style.display = hints.length ? "none" : "block";
      if (nearBody) {
        nearBody.innerHTML = hints.map((h) =>
          `<tr>
            <td><b>${h.mid || "?"}</b></td>
            <td class="dim">${h.fee || "?"}</td>
            <td style="color:var(--amber)">${fmt.num(h.best_weth, 5)}</td>
            <td class="dim">${fmt.num(h.avg_weth, 5)}</td>
            <td class="dim">${fmt.num(h.n, 0)}</td>
          </tr>`
        ).join("");
      }
    }
    const hist = bc.history || [];
    const skipped = bc.skipped || [];
    solBcRowsCache = [].concat(hist, skipped.map((x) => ({ ...x, kind: x.kind || "skip", stage: x.stage || "skip" })));
    solBcRowsCache.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    const body = $("sol-bcast-table") && $("sol-bcast-table").querySelector("tbody");
    const hempty = $("sol-bc-hist-empty");
    let rows = solBcRowsCache;
    if (solBcFilter === "liq") rows = rows.filter((r) => r.kind === "liq");
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
    updateSolBroadcast(sol);
    updateLiqIntel(s, "sol");
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
    bind("sol-proto-filters", "proto-f", (v) => { solOpProto = v; }, renderSolOpps);
    bind("sol-cp-filters", "cp-f", (v) => { solCpFilter = v; }, renderSolComps);
    bind("sol-cp-proto-filters", "proto-f", (v) => { solCpProto = v; }, renderSolComps);
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
    const ethBidEl = $("p-eth-bid-v");
    if (ethBidEl) {
      const er = s.eth_race || {};
      const tm = er.bid_mult || {};
      const c = tm.contested != null ? tm.contested : 1;
      ethBidEl.textContent = c.toFixed(2);
    }
    $("p-ready").textContent = (s.intel ? s.intel.readiness : 0) + "%";
    const bc = s.broadcast || {};
    const ready = bc.ready || {};
    const bcastEl = $("p-bcast");
    if (bcastEl) {
      if (!bc.enabled) bcastEl.textContent = "off";
      else if (ready.liq) bcastEl.textContent = "liq";
      else bcastEl.textContent = "blocked";
      bcastEl.style.color = (!bc.enabled ? "var(--amber)"
        : ready.liq ? "var(--green)" : "var(--red)");
    }
    const raceEl = $("p-race");
    if (raceEl) raceEl.textContent = "--";
    renderFeedPills(s.feeds);
    $("sys-info").textContent = "uptime " + fmt.age(s.started) + " | ws " + (s.now ? "live" : "--");
  };

  const BOT_GROUPS = [
    { id: "observe", label: "observe", keys: ["mempool", "prices"] },
    { id: "scan", label: "scan", keys: ["sweep", "competitors"] },
    { id: "execute", label: "execute", keys: ["broadcast", "funds", "intel"] },
  ];

  function renderFeedPills(feeds) {
    const f = feeds || {};
    [["p-feed-eth", "p-feed-eth-v", f.eth], ["p-feed-sol", "p-feed-sol-v", f.sol]]
      .forEach(([id, vid, feed]) => {
        const el = $(id), val = $(vid);
        if (!el || !val) return;
        const mode = String((feed || {}).mode || "off").toLowerCase();
        val.textContent = mode.toUpperCase();
        el.style.color = mode === "live" ? "var(--green)"
          : mode === "degraded" ? "var(--amber)" : "var(--muted, #888)";
        const provs = (feed || {}).providers || [];
        el.title = provs.length
          ? provs.map((p) => `${p.name}: ok=${p.ok} fail=${p.fail}`).join("\n")
          : "disabled (no ETH_FEED_WSS_URLS / SOL_FEED_WS_URLS)";
      });
  }

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
    const gates = !!ready.liq;

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
        `<span class="bt-pill ${sim ? "sim" : "idle"}" title="Sim mode: dry-run only, no real txs">sim ${sim ? "ON" : "off"}</span>` +
        `<span class="bt-pill ${bc.keep_live ? "live" : "idle"}" title="Keep Live: auto-send +EV liquidations">live ${bc.keep_live ? "ON" : "off"}</span>` +
        `<span class="bt-pill ${armed ? "live" : "idle"}" title="${armed ? "Armed: will broadcast real txs" : "Not armed: manual or sim only"}">${bc.arm_note || (armed ? "armed" : "paused")}</span>` +
        `<span class="bt-pill ${gates ? "ready" : "blocked"}" title="${gates ? "All contracts and keys ready" : "Missing contracts or keys"}">${gates ? "gates open" : "gates closed"}</span>`;
    }

    const wal = $(opts.walletsId);
    if (wal) {
      const tags = { funder: "capital", sponsor: "tips", bot: "fee payer" };
      wal.innerHTML = ["funder", "sponsor", "bot"].map((n) => {
        const f = funds[n] || {};
        const pk = wallets[n] || f.pubkey || "";
        const raw = f[balKey];
        const bal = raw != null ? fmt.num(raw, 4) : "--";
        const low = walletLow(n, f);
        const short = pk ? `${pk.slice(0, 4)}…${pk.slice(-4)}` : "unset";
        return `<span class="bt-wal-inline${low ? " low" : ""}" title="${n} (${tags[n]}): ${bal} ${unit}${low ? " — LOW" : ""}">` +
          `${n}: <b${low ? ' style="color:var(--amber)"' : ""}>${bal}</b> ${unit}` +
          (low ? ` <span class="bt-pill unfunded">low</span>` : "") +
          `</span>`;
      }).join("");
    }

    const parseMsg = (key, raw) => {
      const msg = String(raw || "").trim();
      if (!msg) return { main: "—" };
      if (key === "mempool") {
        const m = msg.match(/(\d[\d,]*)\s*pend/i);
        const q = msg.match(/(\d[\d,]*)\s*q/i);
        const mev = msg.match(/MEV\s+live[=:]\s*(\d+)/i);
        const tps = msg.match(/tps[=:]\s*([\d.]+)/i);
        const parts = [];
        if (m) parts.push(`${m[1]} pend`);
        if (q) parts.push(`${q[1]} q`);
        if (mev) parts.push(`${mev[1]} MEV`);
        if (tps) parts.push(`${tps[1]} tps`);
        return { main: parts.length ? parts.join(" · ") : msg };
      }
      if (key === "prices") {
        const eth = msg.match(/eth[=$\s]+([\d,.]+)/i);
        const sol = msg.match(/sol[=$\s]+([\d,.]+)/i);
        const gas = msg.match(/gas[=:\s]+([\d.]+)/i);
        const blk = msg.match(/blk[=:]\s*([\d,]+)/i);
        const slot = msg.match(/slot[=:]\s*([\d,]+)/i);
        const parts = [];
        if (eth) parts.push(`ETH $${eth[1]}`);
        if (sol) parts.push(`SOL $${sol[1]}`);
        if (gas) parts.push(`${gas[1]} gwei`);
        if (blk) parts.push(`#${blk[1]}`);
        if (slot) parts.push(`slot ${slot[1]}`);
        return { main: parts.length ? parts.join(" · ") : msg };
      }
      if (key === "sweep") {
        const n = msg.match(/(\d+)\s*(opp|watch|liq|probe)/i);
        const proto = msg.match(/(Aave|Spark|Compound|Morpho|Solend)/i);
        const parts = [];
        if (proto) parts.push(proto[1]);
        if (n) parts.push(`${n[1]} ${n[2]}`);
        return { main: parts.length ? parts.join(" · ") : msg };
      }
      if (key === "broadcast") {
        const stage = msg.match(/stage[=:]\s*(\w+)/i);
        const sent = msg.match(/sent[=:]\s*(\d+)/i);
        const sim = msg.match(/sim/i);
        const parts = [];
        if (sim) parts.push("sim only");
        if (stage) parts.push(stage[1]);
        if (sent) parts.push(`${sent[1]} sent`);
        return { main: parts.length ? parts.join(" · ") : (msg || "idle") };
      }
      if (key === "funds") {
        const eq = msg.match(/equity[=$\s]+([\d.]+)/i);
        const pnl = msg.match(/pnl[=:\s]+([\w.]+)/i);
        const low = msg.match(/LOW/i);
        const parts = [];
        if (eq) parts.push(`eq $${eq[1]}`);
        if (pnl) parts.push(`pnl ${pnl[1]}`);
        if (low) parts.push("⚠ low");
        return { main: parts.length ? parts.join(" · ") : msg };
      }
      if (key === "competitors") {
        const n = msg.match(/(\d+)\/(\d+)h/);
        const proto = msg.match(/(Aave\+?\w*|Compound|Morpho|Solend)/i);
        const parts = [];
        if (n) parts.push(`${n[1]} in ${n[2]}h`);
        if (proto) parts.push(proto[1]);
        return { main: parts.length ? parts.join(" · ") : msg };
      }
      if (key === "intel") {
        const recs = msg.match(/(\d+)\s*rec/i);
        const ready = msg.match(/readiness[=:\s]*([\d.]+)/i);
        const parts = [];
        if (recs) parts.push(`${recs[1]} recs`);
        if (ready) parts.push(`${ready[1]}% ready`);
        return { main: parts.length ? parts.join(" · ") : msg };
      }
      return { main: msg };
    };

    el.innerHTML = BOT_GROUPS.map((g) => {
      const tiles = g.keys.map((k) => {
        const b = bots[k] || {};
        const t = toneOf(k, b.status);
        const age = b.last ? fmt.age(b.last) + " ago" : "never";
        const parsed = parseMsg(k, b.msg);
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
          <div class="b-msg">${hx(parsed.main)}</div>
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
        sweep: "HF Opportunity Sweep", competitors: "Competitor Watch",
        intel: "Learning / Intel", broadcast: "Broadcast Submit",
      },
      roles: {
        mempool: "watch", prices: "oracle", funds: "wallets", sweep: "HF",
        competitors: "liq", intel: "learn", broadcast: "submit",
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
      if (p.equity_eth != null) bits.push(`${fmt.num(p.equity_eth, 4)} ETH`);
      if (p.day_realized_usd) bits.push(`24h real $${fmt.num(p.day_realized_usd, 2)}`);
      if (!p.capital_ok) bits.push("fund sponsor + bot to start");
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
      const roles = { funder: "capital", sponsor: "tips", bot: "fee payer" };
      const role = roles[label] || "";
      const eth = f.eth || 0;
      const weth = f.weth || 0;
      const usdc = f.usdc || 0;
      const usdt = f.usdt || 0;
      const parts = [];
      if (eth > 0.0001 || weth > 0.0001) parts.push(`${fmt.num(eth + weth, 4)} ETH`);
      if (usdc > 0) parts.push(`${fmt.num(usdc, 0)} USDC`);
      if (usdt > 0) parts.push(`${fmt.num(usdt, 0)} USDT`);
      const bal = parts.length ? parts.join(" · ") : "empty";
      const cls = (eth + weth) > 0.001 ? "style=\"color:var(--green)\"" : ((eth + weth) > 0 ? "style=\"color:var(--amber)\"" : "");
      const short = addr ? `${addr.slice(0, 6)}&hellip;${addr.slice(-4)}` : "--";
      return `<tr><td>${label.toUpperCase()} <span class="tag">${role}</span></td>` +
             `<td class="mono copy" data-addr="${addr}" title="click to copy">${short}</td>` +
             `<td ${cls}>${bal}</td></tr>`;
    }).join("");
    $("funds-table").innerHTML = `<table class="mini"><thead><tr><th>wallet</th><th>address</th><th>balances</th></tr></thead><tbody>${rows}</tbody></table>`;
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
    if (mevNote) mevNote.textContent = `${live.length}/${mpLiveCache.length} · decoded`;
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
    const mevN = meta.mev_live != null ? meta.mev_live : (m.mev_txs || []).length;
    $("mp-queued").textContent = fmt.num(mevN, 0);
    const liveN = meta.mev_live != null ? meta.mev_live : (m.mev_txs || []).length;
    const liveNEl = $("mp-mev-live-n");
    if (liveNEl) liveNEl.textContent = fmt.num(liveN, 0);
    const gasEl = $("mp-mev-share");
    if (gasEl) {
      const gas = s.gas_gwei != null ? s.gas_gwei : (s.eth_price_usd != null ? null : null);
      gasEl.textContent = gas != null ? fmt.num(gas, 1) : "--";
    }

    const pr = $("mp-pressure");
    if (pr) {
      const p = meta.pressure || "idle";
      pr.textContent = p;
      pr.className = "mp-pressure-badge " + p;
    }
    const metaEl = $("mp-meta");
    if (metaEl) {
      metaEl.innerHTML =
        `<span>sampled <b>${fmt.num(meta.sampled, 0)}</b></span>` +
        (meta.content_age_s != null
          ? `<span>age <b>${fmt.num(meta.content_age_s, 0)}s</b></span>`
          : "") +
        `<span>MEV <b>${fmt.num(meta.mev_share_pct, 1)}%</b></span>` +
        `<span>contested <b style="color:${meta.contested ? "var(--red)" : "var(--dim)"}">${fmt.num(meta.contested, 0)}</b></span>` +
        `<span class="dim">${m.method || meta.method || "txpool_content"}</span>`;
    }

    const mv = m.mev || {};
    const order = ["liq", "spoke", "aave"];
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
    if (spokeNote) {
      const protoCounts = {};
      spoke.forEach((t) => { const p = t.proto || "other"; protoCounts[p] = (protoCounts[p] || 0) + 1; });
      const protoTags = Object.entries(protoCounts).map(([k, v]) => `${k} ${v}`).join(" · ");
      spokeNote.textContent = `${spoke.length} txs${protoTags ? " · " + protoTags : ""} · ${(m.contested || []).length} contested`;
    }
    const spokeEmpty = $("mp-spoke-empty");
    if (spokeEmpty) spokeEmpty.style.display = spoke.length ? "none" : "block";
    const protoColors = { "aave-v3": "var(--green)", "aave-v4": "var(--cyan)", "spark": "var(--violet)", "compound": "var(--amber)", "morpho": "var(--red)" };
    $("mp-spoke").querySelector("tbody").innerHTML = spoke.slice(0, 20).map((t) => {
      const hot = t.hot || /liquidat/i.test(t.name || "");
      const flag = hot ? `<span class="pill warn">LIQ</span>` : "";
      const user = t.user_short || (t.user ? t.user.slice(0, 10) + "…" : "--");
      const pc = protoColors[t.proto] || "var(--dim)";
      return `<tr>
        <td style="color:${pc}"><b>${t.proto_label || t.proto || "?"}</b></td>
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
        const barCls = obj.mev || obj.kind === "lending" ? "mev" : "";
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
  let opWatchCache = [];
  let opLastMeta = {};
  const OP_PAIR_COLORS = ["#22c55e", "#22d3ee", "#f59e0b", "#a78bfa", "#ef4444", "#3b82f6", "#ec4899", "#14b8a6"];
  const isSendLeftover = (t) => /KIND\(\)|DEPLOY\.md|GenericFlashLiquidator|LIQ_GENERIC|LIQ_CONTRACT unset/i.test(String(t));
  const fmtHfCell = (hf) => {
    if (hf == null || !Number.isFinite(hf)) return "—";
    if (hf >= 100) return "∞";
    return hf < 10 ? hf.toFixed(4).replace(/0+$/, "").replace(/\.$/, "") : hf.toFixed(3);
  };

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
    if (o.submit === "blocked") {
      const why = o.submit_reason || o.live_block_reason || GATE_TITLE_ETH;
      bits.push(`<span class="pill blocked" title="${String(why).replace(/"/g, "'")}">blocked</span>`);
    } else if (o.submit === "sim")
      bits.push(`<span class="pill" title="${(o.submit_reason || "sim-only · hunt on").replace(/"/g, "'")}">sim</span>`);
    if (o.race || o.contested)
      bits.push(`<span class="pill warn" title="mempool or recent competitor">race</span>`);
    if (o.recent_competitor) bits.push(`<span class="pill">comp</span>`);
    if (o.edge) bits.push(`<span class="pill accent">${o.edge}</span>`);
    if (o.flash_fee_bps != null && Number(o.flash_fee_bps) > 0)
      bits.push(`<span class="pill" title="${o.flash_note || "Aave V3 flashLoan"}">${fmt.num(o.flash_fee_bps, 0)} bps flash</span>`);
    const leftTxt = leftoverTxt(o);
    if (leftTxt) bits.push(`<span class="pill blocked" title="${leftTxt.replace(/"/g, "'")}">leftover</span>`);
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
    const watchN = opWatchCache.length;
    if (note) {
      note.textContent = opCache.length
        ? `${rows.length}/${opCache.length} in feed · HF<1 +EV`
        : `0 in feed · ${watchN} in watch`;
    }
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
        const fatal = m.status === "error" && !(Number(m.scanned) > 0);
        if (fatal && (m.errors && m.errors.length)) {
          empty.classList.add("err");
          empty.textContent = "sweep error: " + m.errors[0];
        } else if (opFilter !== "all" || opProto !== "all") {
          empty.classList.remove("err");
          empty.textContent = `no HF<1 +EV match this filter`
            + (opProto !== "all" ? ` · ${opProto}` : "")
            + (opFilter !== "all" ? ` · ${opFilter}` : "")
            + ` · ${opCache.length} in feed · ${watchN} in watch`;
        } else if (m.last_scan) {
          empty.classList.remove("err");
          empty.textContent = `no HF<1 +EV this sweep · watch ${fmt.num(watchN, 0)}`
            + (m.last_scan ? ` · last scan ${fmt.age(m.last_scan)} ago` : "");
        } else {
          empty.classList.remove("err");
          empty.textContent = "scanning Aave · Spark · Compound · Morpho for HF<1 +EV…";
        }
      }
    }
    body.innerHTML = rows.slice(0, 80).map((o) => {
      const user = o.user || "";
      const short = user ? `${user.slice(0, 6)}…${user.slice(-4)}` : "--";
      const hf = oppHf(o);
      const urg = hfUrgency(hf);
      const collUsd = oppUsd(o, "coll");
      const debtUsd = oppUsd(o, "debt");
      const pair = `${o.coll_sym || "?"}→${o.debt_sym || "?"}`;
      const net = o.net_usd != null ? o.net_usd : o.profit_usd;
      const netColor = net == null ? "var(--dim)" : Number(net) > 0 ? "var(--green)" : "var(--dim)";
      return `<tr>
        <td>${protoPill(o)}</td>
        <td class="mono copy" data-addr="${user}" title="${user}">${short}</td>
        <td class="${hfClass(hf)}">${fmtHfCell(hf)}</td>
        <td title="${pair}">${collUsd != null ? fmt.usd(collUsd) : "—"}</td>
        <td title="${pair}">${debtUsd != null ? fmt.usd(debtUsd) : "—"}</td>
        <td><span class="op-urg ${urg.cls}">${urg.label}</span></td>
        <td style="color:var(--amber)">${o.bonus_usd != null ? fmt.usd(o.bonus_usd) : "—"}</td>
        <td style="color:${netColor}"><b>${net != null && Number.isFinite(Number(net)) ? fmt.usd(net) : "—"}</b></td>
        <td>${liqFlagBits(o)}</td>
      </tr>`;
    }).join("");
  };

  const renderWatch = () => {
    const rows = opWatchCache.filter((w) => opProto === "all" || protoId(w) === opProto);
    const note = $("watch-note");
    if (note) note.textContent = `${rows.length}/${opWatchCache.length} · up to 50 lowest HF`;
    const wempty = $("watch-empty");
    if (wempty) {
      if (rows.length) wempty.style.display = "none";
      else {
        wempty.style.display = "block";
        wempty.textContent = opWatchCache.length
          ? `no closest HF for ${opProto} this sweep`
          : "waiting for lowest-HF hydrates…";
      }
    }
    const wbody = $("watch-table") && $("watch-table").querySelector("tbody");
    if (!wbody) return;
    wbody.innerHTML = rows.map((w) => {
      const hf = oppHf(w);
      const collUsd = oppUsd(w, "coll");
      const debtUsd = oppUsd(w, "debt");
      const urg = hfUrgency(hf);
      const user = w.user || "";
      const short = user ? `${user.slice(0, 6)}…${user.slice(-4)}` : "--";
      const left = leftoverTxt(w);
      const urgCell = left
        ? `<span class="op-urg ${urg.cls}">${urg.label}</span> <span class="pill blocked" title="${left.replace(/"/g, "'")}">leftover</span>`
        : `<span class="op-urg ${urg.cls}">${urg.label}</span>`;
      return `<tr>
        <td>${protoPill(w)}</td>
        <td class="mono copy" data-addr="${user}" title="click to copy">${short}</td>
        <td class="${hfClass(hf)}">${fmtHfCell(hf)}</td>
        <td>${collUsd != null ? fmt.usd(collUsd) : "—"}</td>
        <td>${debtUsd != null ? fmt.usd(debtUsd) : "—"}</td>
        <td>${urgCell}</td>
      </tr>`;
    }).join("");
  };

  const hfUrgency = (hf) => {
    if (hf == null || hf >= 100) return { cls: "ok", label: "—", bar: 0 };
    if (hf < 1.0) return { cls: "crit", label: "liq", bar: 100 };
    if (hf < 1.05) return { cls: "hot", label: "hot", bar: 90 };
    if (hf < 1.1) return { cls: "warn", label: "warn", bar: 65 };
    if (hf < 1.25) return { cls: "ok", label: "ok", bar: 35 };
    return { cls: "ok", label: "safe", bar: 12 };
  };

  const hfClass = (hf) => {
    if (hf == null) return "op-hf-ok";
    if (hf < 1.0) return "op-hf-crit";
    if (hf < 1.05) return "op-hf-hot";
    if (hf < 1.1) return "op-hf-warn";
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
      renderWatch();
    });
  }

    const updateOpps = (s) => {
    const opps = s.opportunities || [];
    const wl = (s.watchlist || []).filter((w) => {
      const hf = Number(w.hf);
      return Number.isFinite(hf) && hf >= 1e18 && hf < 1e38;
    }).slice(0, 50);
    const m = s.opportunities_meta || {};
    opLastMeta = m;
    opCache = opps.slice(0, 80);
    opWatchCache = wl;

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
    setTxt("op-best", best != null && best > 0 ? fmt.usd(best) : "—");
    setTxt("op-edge-n", fmt.num(edgeN, 0));
    setTxt("op-sweep", fmt.num(m.watch_n != null ? m.watch_n : wl.length, 0));

    const closest = wl[0];
    const closestHf = closest ? oppHf(closest) : null;
    const closestEl = $("op-closest-hf");
    if (closestEl) {
      closestEl.textContent = fmtHfCell(closestHf);
      closestEl.className = "big " + (closestHf != null && closestHf < 1.05 ? "red" : closestHf != null && closestHf < 1.1 ? "amber" : "dim");
    }
    const cu = $("op-closest-user");
    if (cu) {
      const addr = closest && closest.user ? closest.user : "";
      cu.textContent = addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : "—";
      cu.className = "dim mono copy";
      if (addr) { cu.dataset.addr = addr; cu.title = addr; }
    }

    const urg = $("op-urgency");
    if (urg) {
      const buckets = [
        { label: "<1.00", n: wl.filter((w) => { const h = oppHf(w); return h != null && h < 1; }).length },
        { label: "1–1.05", n: wl.filter((w) => { const h = oppHf(w); return h != null && h >= 1 && h < 1.05; }).length },
        { label: "1.05–1.1", n: wl.filter((w) => { const h = oppHf(w); return h != null && h >= 1.05 && h < 1.1; }).length },
        { label: "1.1+", n: wl.filter((w) => { const h = oppHf(w); return h != null && h >= 1.1; }).length },
      ];
      const maxN = Math.max(1, ...buckets.map((b) => b.n));
      urg.innerHTML = buckets.map((b) =>
        `<div class="op-urg-row"><span>${b.label}</span>` +
        `<div class="op-urg-bar"><i style="width:${Math.round(100 * b.n / maxN)}%"></i></div>` +
        `<span>${b.n}</span></div>`).join("");
    }

    paintOpGate("op-gate", m, "eth");
    const meta = $("op-meta");
    if (meta) {
      const sweepBot = (s.bots || {}).sweep || {};
      meta.innerHTML =
        (m.scanned != null ? `<span>scanned <b>${fmt.num(m.scanned, 0)}</b></span>` : "") +
        (sweepBot.status ? `<span>sweep <b>${sweepBot.status}</b></span>` : "") +
        (m.flash_fee_bps != null ? `<span>flash <b>${fmt.num(m.flash_fee_bps, 0)} bps ${m.flash_fee_src || "aave-v3"}</b></span>` : "") +
        (m.last_block ? `<span>blk <b>${m.last_block}</b></span>` : "") +
        (m.last_scan ? `<span>${fmt.age(m.last_scan)} ago</span>` : "");
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
        : `<span class="dim">no liquidatable pairs · see watch →</span>`;
    }

    const leftoverEl = $("op-leftover");
    if (leftoverEl) {
      const bits = (m.leftovers || []).filter((t) => t && !isSendLeftover(t));
      if (bits.length) {
        leftoverEl.style.display = "";
        leftoverEl.textContent = "leftover · " + bits.slice(0, 4).join(" · ");
      } else {
        leftoverEl.style.display = "none";
        leftoverEl.textContent = "";
      }
    }

    renderOppsFeed();
    renderWatch();
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
      if (cpFilter === "profit") return rowEstUsd(c) != null || rowNetUsd(c) != null;
      if (cpFilter === "revert") return c.status === 0;
      return true;
    });
    const note = $("cp-feed-note");
    if (note) note.textContent = feedWindowNote(
      rows.length, cpCache.length, (cpLastMeta || {}).count_1h,
      cpFilter !== "all" || cpProto !== "all");
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
          empty.textContent = "scan error: " + String(m.status).replace(/^err\s*/i, "");
        } else if (cpFilter !== "all" || cpProto !== "all") {
          empty.classList.remove("err");
          empty.textContent = `no rows match filter`
            + (cpProto !== "all" ? ` ${cpProto}` : "")
            + (cpFilter !== "all" ? ` ${cpFilter}` : "")
            + ` · ${cpCache.length} in feed · ${fmt.num(m.count_1h, 0)} in last hour`;
        } else if (m.last_scan || m.last_block) {
          empty.classList.remove("err");
          empty.textContent = `no confirmed liquidations in feed yet`
            + (m.n_logs != null ? ` · this window ${fmt.num(m.n_logs, 0)}` : "")
            + (m.from_block && m.to_block ? ` · blk ${m.from_block}→${m.to_block}` : "")
            + (m.last_scan ? ` · ${fmt.age(m.last_scan)} ago` : "");
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
      const est = rowEstUsd(c);
      const net = rowNetUsd(c);
      const flags = [];
      if (c.missed_by_us)
        flags.push(`<span class="cp-flag miss" title="we saw this confirmed liq and did not send">miss</span>`);
      if (c.edge)
        flags.push(`<span class="cp-flag edge" title="long-tail pair">edge</span>`);
      if (c.status === 0) flags.push(`<span class="cp-flag revert">revert</span>`);
      const left = cpLeftoverBits(c);
      if (left.length)
        flags.push(`<span class="cp-flag left" title="${left.join(", ")}">leftover</span>`);
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
        <td class="dim" title="on-chain gas × ETH price — not their profit">${c.gas_cost_usd != null ? fmt.usd(c.gas_cost_usd) : "—"}</td>
        <td style="color:${est != null ? "var(--amber)" : "var(--dim)"}">${fmtHonestUsd(est)}</td>
        <td style="color:${net != null ? "var(--green)" : "var(--dim)"}"><b>${fmtHonestUsd(net)}</b></td>
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
    const feedN = cpCache.length || m.total || 0;
    const h1 = m.count_1h || 0;
    setTxt("cp-count", fmt.num(h1, 0));
    setTxt("cp-count-feed", h1 === 0 && feedN ? `${fmt.num(feedN, 0)} in feed` : "");
    setTxt("cp-searchers", fmt.num(m.unique_searchers, 0));
    const allSearcherN = cpCache.length ? [...new Set(cpCache.map(c => c.searcher || c.liquidator || "").filter(Boolean))].length : 0;
    setTxt("cp-searchers-feed", !(m.unique_searchers) && allSearcherN ? `${allSearcherN} all time` : "");
    setTxt("cp-sum-est", honestUsd(m.sum_est_profit) != null ? fmt.usd(m.sum_est_profit) : "\u2014");
    setTxt("cp-missed",
      m.missed_by_us
        ? `${fmt.num(m.missed_by_us, 0)}${m.miss_rate_pct ? ` \u00b7 ${fmt.num(m.miss_rate_pct, 0)}%` : ""}`
        : "0");

    const meta = $("cp-meta");
    if (meta) {
      const hit = lastHitTs(m, cpCache);
      meta.innerHTML =
        `<span>${cpScanLine(m, cpCache.length, hit)}</span>` +
        (m.avg_gas != null ? `<span>avg gas <b>${fmt.num(m.avg_gas, 0)}</b></span>` : "") +
        (honestUsd(m.sum_est_profit) != null
          ? `<span>Σ our est <b style="color:var(--amber)">${fmt.usd(m.sum_est_profit)}</b></span>`
          : "") +
        `<span>edge <b>${fmt.num(m.edge_n, 0)}</b></span>` +
        `<span>reverts <b>${fmt.num(m.revert_n, 0)}</b></span>` +
        `<span>spokes <b>${m.spokes || 0}</b></span>` +
        `<span>tracked <b>${fmt.num(m.total, 0)}</b></span>` +
        (m.from_block && m.to_block ? `<span>blk <b>${m.from_block}–${m.to_block}</b></span>` : "") +
        (m.last_scan ? `<span>${fmt.age(m.last_scan)} ago</span>` : "") +
        (m.status && m.status !== "ok" ? `<span style="color:var(--red)">${m.status}</span>` : "");
    }
    const leftEl = $("cp-leftover");
    if (leftEl) {
      const bits = (m.errors || m.leftovers || []).filter(Boolean);
      if (bits.length && m.status && String(m.status).startsWith("err")) {
        leftEl.style.display = "";
        leftEl.textContent = "leftover · " + bits.slice(0, 3).join(" · ");
      } else {
        leftEl.style.display = "none";
        leftEl.textContent = "";
      }
    }

    const mix1h = m.pair_mix || [];
    const now2 = Math.floor(Date.now() / 1000);
    const mix = mix1h.length ? mix1h : pairMixFromRows(cpCache, now2, m.last_slot || m.last_block);
    const mixLabel = mix1h.length ? "last hour" : "all time";
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
      ).join("") || `<span class="dim">no confirmed liquidations</span>`;
    }
    const mixLabelEl = $("cp-mix-label");
    if (mixLabelEl) mixLabelEl.textContent = `pair mix (${mixLabel})`;

    const { tops: cpTops, label: cpTopLabel } = resolveTopSearchers(m, cpCache, m.last_slot || m.last_block);
    if (cpTops.length && !(m.unique_searchers))
      setTxt("cp-searchers", fmt.num(cpTops.length, 0));
    renderTopSearchers({
      tableId: "cp-searcher-table",
      emptyId: "cp-searcher-empty",
      tops: cpTops,
      meta: m,
      label: cpTopLabel,
    });

    const pNote = $("cp-pair-note");
    if (pNote) pNote.textContent = String(mix.length);
    const pTag = $("cp-pair-tag");
    if (pTag) pTag.textContent = mixLabel;
    const pBody = $("cp-pair-table") && $("cp-pair-table").querySelector("tbody");
    if (pBody) {
      pBody.innerHTML = mix.map((p) =>
        `<tr>
          <td><b>${p.pair}</b></td>
          <td><div class="cp-bar-track"><div class="cp-bar pair" style="width:${Math.min(100, p.pct || 0)}%"></div></div></td>
          <td>${fmt.num(p.n, 0)}</td>
          <td class="dim">${fmt.num(p.pct, 0)}%</td>
        </tr>`
      ).join("") || `<tr><td colspan="4" class="dim">no confirmed liquidations</td></tr>`;
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

  const hfBucket = (hf) => {
    const n = Number(hf);
    if (n == null || Number.isNaN(n)) return null;
    const h = n > 1e9 ? n / 1e18 : n;
    if (h < 1) return "<1.0";
    if (h < 1.05) return "1.0-1.05";
    if (h < 1.1) return "1.05-1.1";
    return ">1.1";
  };
  const protoKey = (row) => {
    const p = String(row.protocol_id || row.protocol || row.proto_label || "").toLowerCase();
    if (p.includes("compound")) return "compound_v3";
    if (p.includes("morpho")) return "morpho";
    if (p.includes("spark")) return "spark";
    if (p.includes("solend")) return "solend";
    if (p.includes("kamino")) return "kamino";
    if (p.includes("marginfi") || p.includes("margin fi")) return "marginfi";
    if (p.includes("drift")) return "drift";
    return "aave_v3";
  };
  const LIQ_HF_BUCKETS = ["<1.0", "1.0-1.05", "1.05-1.1", ">1.1"];
  const mergeHealthDist = (a, b) => {
    const out = {};
    let sumA = 0;
    let sumB = 0;
    LIQ_HF_BUCKETS.forEach((k) => {
      const av = Number((a || {})[k]) || 0;
      const bv = Number((b || {})[k]) || 0;
      sumA += av;
      sumB += bv;
      out[k] = 0;
    });
    const src = sumB > 0 ? b : a;
    LIQ_HF_BUCKETS.forEach((k) => { out[k] = Number((src || {})[k]) || 0; });
    return out;
  };
  const mergeProtocols = (a, b) => {
    const out = {};
    const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
    keys.forEach((k) => {
      const av = (a || {})[k] || {};
      const bv = (b || {})[k] || {};
      const count = Math.max(Number(av.count) || 0, Number(bv.count) || 0);
      const volume = Math.max(Number(av.volume) || 0, Number(bv.volume) || 0);
      if (count || volume) out[k] = { count, volume };
    });
    return out;
  };
  const mergeLiqIntel = (backend, live) => {
    const b = backend || {};
    const l = live || {};
    const useLiveVol = (Number(l.count_24h) || 0) > 0;
    const hist = (b.volume_history && b.volume_history.length)
      ? b.volume_history
      : (l.volume_history || []);
    return {
      volume_24h: useLiveVol ? l.volume_24h : (Number(b.volume_24h) || Number(l.volume_24h) || 0),
      count_24h: useLiveVol ? l.count_24h : (Number(b.count_24h) || Number(l.count_24h) || 0),
      avg_size: useLiveVol ? l.avg_size : (Number(b.avg_size) || Number(l.avg_size) || 0),
      gas_per_liq: Number(l.gas_per_liq) || Number(b.gas_per_liq) || 0,
      protocols: mergeProtocols(b.protocols, l.protocols),
      health_dist: mergeHealthDist(b.health_dist, l.health_dist),
      competitors: Object.assign({}, b.competitors || {}, l.competitors || {}),
      volume_history: hist,
      pressure: l.pressure || b.pressure || "idle",
    };
  };
  const liveLiqIntel = (src, backend) => {
    const comps = src.competitors || [];
    const meta = src.competitors_meta || {};
    const watch = src.watchlist || [];
    const opps = src.opportunities || [];
    const now = Math.floor(Date.now() / 1000);
    const cutoff = now - 86400;
    const protocols = {
      aave_v3: { count: 0, volume: 0 },
      compound_v3: { count: 0, volume: 0 },
      morpho: { count: 0, volume: 0 },
      spark: { count: 0, volume: 0 },
      solend: { count: 0, volume: 0 },
      kamino: { count: 0, volume: 0 },
      marginfi: { count: 0, volume: 0 },
      drift: { count: 0, volume: 0 },
    };
    const health = { "<1.0": 0, "1.0-1.05": 0, "1.05-1.1": 0, ">1.1": 0 };
    let volume = 0, count = 0, gasSum = 0, gasN = 0;
    const buckets = {};
    comps.forEach((c) => {
      const ts = Number(c.ts) || now;
      if (ts < cutoff) return;
      const pk = protoKey(c);
      const vol = Number(c.coll_usd || c.est_profit_usd || c.est || 0) || 0;
      protocols[pk].count += 1;
      protocols[pk].volume += vol;
      volume += vol;
      count += 1;
      const g = Number(c.gas_cost_usd || c.gas_usd || 0) || 0;
      if (g) { gasSum += g; gasN += 1; }
      const b = Math.floor(ts / 300) * 300;
      buckets[b] = (buckets[b] || 0) + (vol || 1);
    });
    watch.concat(opps).forEach((o) => {
      const b = hfBucket(o.hf != null ? o.hf : o.health_factor);
      if (b) health[b] += 1;
    });
    const missPct = Number(meta.miss_rate_pct);
    const missed = Number(meta.missed_by_us) || 0;
    const n1h = Number(meta.count_1h) || count;
    const rate = Number.isFinite(missPct)
      ? Math.max(0, Math.min(1, 1 - missPct / 100))
      : (n1h ? Math.max(0, (n1h - missed) / n1h) : 0);
    let pressure = meta.pressure || "idle";
    if (n1h >= 12) pressure = "hot";
    else if (n1h >= 5) pressure = "busy";
    else if (n1h >= 1) pressure = "quiet";
    const hist = (backend && backend.volume_history) || [];
    const volume_history = Object.keys(buckets).sort().map((t) => ({ ts: Number(t), volume: buckets[t] }));
    return mergeLiqIntel(backend, {
      volume_24h: volume,
      count_24h: count,
      avg_size: count ? volume / count : 0,
      gas_per_liq: gasN ? gasSum / gasN : 0,
      protocols,
      health_dist: health,
      competitors: {
        searchers: Number(meta.unique_searchers) || 0,
        success_rate: rate,
        missed,
      },
      volume_history: volume_history.length ? volume_history : hist,
      pressure,
    });
  };

  const updateLiqIntel = (s, prefix) => {
    const src = prefix === "sol" ? (s.sol || {}) : s;
    const intel = src.intel || (prefix === "sol" ? null : s.intel) || {};
    const li = liveLiqIntel(src, intel.liq_intel);
    if (!li) return;

    const pfx = prefix ? prefix + "-" : "";
    const el = (id) => $(pfx + id);
    const fmtK = (v) => {
      const n = Number(v) || 0;
      if (n >= 1000000) return "$" + (n / 1000000).toFixed(2) + "m";
      if (n >= 1000) return "$" + (n / 1000).toFixed(1) + "k";
      return "$" + n.toFixed(0);
    };

    const volEl = el("liq-volume"); if (volEl) volEl.textContent = fmtK(li.volume_24h);
    const cntEl = el("liq-count"); if (cntEl) cntEl.textContent = fmt.num(li.count_24h, 0);
    const avgEl = el("liq-avg"); if (avgEl) avgEl.textContent = fmtK(li.avg_size);
    const gasEl = el("liq-gas"); if (gasEl) gasEl.textContent = fmt.usd(li.gas_per_liq);

    const protoBar = el("liq-proto-bar");
    const protoLabels = el("liq-proto-labels");
    if (protoBar && li.protocols) {
      const colors = {
        aave_v3: "#22d3ee", compound_v3: "#22c55e", morpho: "#a78bfa", spark: "#f59e0b",
        solend: "#c084fc", kamino: "#38bdf8", marginfi: "#4ade80", drift: "#fb7185",
      };
      const names = {
        aave_v3: "Aave", compound_v3: "Compound", morpho: "Morpho", spark: "Spark",
        solend: "Solend", kamino: "Kamino", marginfi: "MarginFi", drift: "Drift",
      };
      const entries = Object.entries(li.protocols).filter(([, v]) => (v && v.count) || 0);
      const total = entries.reduce((sum, [, p]) => sum + (p.count || 0), 0) || 1;
      if (!entries.length) {
        protoBar.innerHTML = `<div style="width:100%;background:#1e293b"></div>`;
        if (protoLabels) protoLabels.innerHTML = `<span class="dim">no confirmed liqs yet</span>`;
      } else {
        protoBar.innerHTML = entries.map(([k, v]) =>
          `<div style="width:${(v.count / total * 100).toFixed(1)}%;background:${colors[k] || "#64748b"}"></div>`
        ).join("");
        if (protoLabels) protoLabels.innerHTML = entries.map(([k, v]) =>
          `<span style="color:${colors[k]}">${names[k] || k} ${v.count}</span>`
        ).join("");
      }
    }

    const healthEl = el("liq-health-chart");
    if (healthEl) {
      const hd = li.health_dist || {};
      const rows = [
        ["<1.0", "#ef4444", "liq"],
        ["1.0-1.05", "#f59e0b", "crit"],
        ["1.05-1.1", "#22c55e", "near"],
        [">1.1", "#64748b", "ok"],
      ];
      const maxN = Math.max(1, ...rows.map(([k]) => Number(hd[k]) || 0));
      const any = rows.some(([k]) => (Number(hd[k]) || 0) > 0);
      if (!any) {
        healthEl.innerHTML = `<div class="empty">no HF samples on watch/feed yet</div>`;
      } else {
        healthEl.innerHTML = rows.map(([k, color]) => {
          const n = Number(hd[k]) || 0;
          const pct = Math.max(n ? 4 : 0, (n / maxN) * 100);
          return `<div class="liq-hf-row">
            <span class="liq-hf-lab">${k}</span>
            <div class="liq-hf-track"><div class="liq-hf-fill" style="width:${pct}%;background:${color}"></div></div>
            <span class="liq-hf-n">${n}</span>
          </div>`;
        }).join("");
      }
    }

    const comp = li.competitors || {};
    const sEl = el("liq-comp-searchers"); if (sEl) sEl.textContent = fmt.num(comp.searchers, 0);
    const rEl = el("liq-comp-rate"); if (rEl) rEl.textContent = ((comp.success_rate || 0) * 100).toFixed(0) + "%";
    const mEl = el("liq-comp-missed"); if (mEl) mEl.textContent = fmt.num(comp.missed, 0);

    const volChart = prefix === "sol" ? solLiqVolumeChart : liqVolumeChart;
    if (volChart) {
      const hist = li.volume_history || [];
      volChart.data.labels = hist.map((h) => fmt.ts(h.ts));
      volChart.data.datasets[0].data = hist.map((h) => Number(h.volume) || 0);
      volChart.update("none");
    }

    const pBadge = el("in-pressure");
    if (pBadge) {
      const pr = li.pressure || "idle";
      pBadge.textContent = pr;
      pBadge.className = "liq-pressure-badge " + pr;
    }
    const tag = $(pfx + "liq-intel-tag");
    if (tag) tag.textContent = `${fmt.num(li.count_24h, 0)} / 24h`;

    if (prefix !== "sol") {
      const pc = s.precompute || {};
      const ethPc = pc.eth || {};
      const solPc = pc.sol || {};
      const pcEthHits = $("pc-eth-hits");
      const pcEthPos = $("pc-eth-pos");
      const pcEthBlock = $("pc-eth-block");
      const pcSolHits = $("pc-sol-hits");
      const pcSolPos = $("pc-sol-pos");
      if (pcEthHits) pcEthHits.textContent = fmt.num(ethPc.hits, 0);
      if (pcEthPos) pcEthPos.textContent = fmt.num(ethPc.positions, 0);
      if (pcEthBlock) pcEthBlock.textContent = ethPc.last_block ? fmt.num(ethPc.last_block, 0) : "\u2014";
      if (pcSolHits) pcSolHits.textContent = fmt.num(solPc.hits, 0);
      if (pcSolPos) pcSolPos.textContent = fmt.num(solPc.positions, 0);
    }
    requestAnimationFrame(() => resizeLiqCharts(prefix === "sol" ? "sol" : "eth"));
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
    if (!st || !rs) return;

    let pressure = sum.pressure || bc.pressure;
    let label = sum.label;
    if (!pressure) {
      if (!bc.enabled) { pressure = "idle"; label = label || "off"; }
      else if (bc.armed && ready.liq) {
        pressure = "hot";
        label = label || (bc.keep_live ? "armed · auto-renew" : "armed live");
      }
      else if (bc.armed) { pressure = "elevated"; label = label || "armed · blocked"; }
      else if (bc.sim_only && ready.liq) { pressure = "quiet"; label = label || "sim ready"; }
      else if (bc.sim_only) { pressure = "busy"; label = label || "sim · blocked"; }
      else if (ready.liq) { pressure = "elevated"; label = label || "ready · disarm"; }
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
    const modeLabel = bc.armed ? (bc.keep_live ? "keep live" : "armed") : bc.sim_only ? "sim" : bc.keep_live ? "keep live" : "off";
    setTxt("bc-mode", modeLabel, bc.armed ? "green" : (bc.sim_only ? "" : "dim"));
    setTxt("bc-dyn-liq", bc.dyn_min_liq != null ? "$" + fmt.num(bc.dyn_min_liq) : "--", "amber");
    const lastStage = sum.last_stage || "--";
    const okStage = ["sent", "ok", "simulated"].includes(String(lastStage).toLowerCase());
    setTxt("bc-last-stage", lastStage, okStage ? "green" : (lastStage === "--" ? "dim" : "amber"));

    st.innerHTML =
      `<span>mode <b>${bc.enabled ? "ON" : "OFF"}</b></span>` +
      `<span>peak <b style="color:${bc.peak_hour ? "var(--amber)" : "var(--dim)"}">${bc.peak_hour ? "YES" : "no"}</b></span>` +
      (bc.brain_advice ? `<span>brain <b style="color:var(--cyan)">${bc.brain_advice}</b></span>` : "") +
      `<span>sponsor <b>${fmt.num(bc.sponsor_target_eth, 3)} ETH</b></span>` +
      (bc.liq_contract ? `<span>liq <b>${bc.liq_contract.slice(0, 10)}…</b></span>` : "") +
      `<span>keep live <b>${bc.keep_live ? "ON" : "off"}</b></span>` +
      `<span>${bc.arm_note || (bc.armed ? "armed" : "not armed")}</span>`;

    const pills = $("bc-mode-pills");
    if (pills) {
      pills.innerHTML =
        `<span class="bc-pill ${bc.sim_only ? "on" : ""}">sim ${bc.sim_only ? "ON" : "off"}</span>` +
        `<span class="bc-pill ${bc.keep_live ? "keep" : ""}">keep live ${bc.keep_live ? "auto-renew" : "off"}</span>` +
        `<span class="bc-pill ${bc.armed ? "live" : ""}">${bc.arm_note || (bc.armed ? "armed LIVE" : "not armed")}</span>` +
        `<span class="bc-pill ${bc.edge_bias ? "on" : ""}">edge ${bc.edge_bias ? "on" : "off"}</span>` +
        `<span class="bc-pill ${ready.liq ? "ok" : "warn"}">${ready.liq ? "gates clear" : "gates blocked"}</span>`;
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
      readyNote.textContent = ready.liq ? "ready" : "blocked";
    }
    rs.innerHTML = (ready.reasons || []).length
      ? ready.reasons.map((r) => `<span style="color:var(--amber)">${r}</span>`).join("")
      : "<span style=\"color:var(--green)\">ready to submit</span>";

    const nearBody = $("bc-near-table") && $("bc-near-table").querySelector("tbody");
    if (nearBody || $("bc-near-empty")) {
      const nearEmpty = $("bc-near-empty");
      const nearNote = $("bc-near-note");
      const hints = bc.near_miss_hints || [];
      if (nearNote) nearNote.textContent = hints.length ? `${hints.length} pairs` : "learning";
      if (nearEmpty) nearEmpty.style.display = hints.length ? "none" : "block";
      if (nearBody) {
        nearBody.innerHTML = hints.map((h) =>
          `<tr>
            <td><b>${h.mid || "?"}</b></td>
            <td class="dim">${h.fee || "?"}</td>
            <td style="color:var(--amber)">${fmt.num(h.best_weth, 5)}</td>
            <td class="dim">${fmt.num(h.avg_weth, 5)}</td>
            <td class="dim">${fmt.num(h.n, 0)}</td>
          </tr>`
        ).join("");
      }
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
  };

  const updateHybrid = (s) => {
    const h = s.hybrid_execution || {};
    const phaseEl = $("hybrid-phase");
    const rateEl = $("hybrid-success-rate");
    const attemptsEl = $("hybrid-attempts");
    const toggleEl = $("hybrid-toggle");
    if (phaseEl) {
      const phase = (h.phase || "idle").toUpperCase();
      phaseEl.textContent = phase;
      phaseEl.className = "status-text" + (phase === "IDLE" ? " dim" : "");
    }
    if (rateEl) {
      const stats = h.stats || {};
      rateEl.textContent = ((stats.success_rate || 0) * 100).toFixed(1) + "%";
    }
    if (attemptsEl) {
      const stats = h.stats || {};
      attemptsEl.textContent = String(stats.total_attempts || 0);
    }
    if (toggleEl && !toggleEl.matches(":hover")) {
      toggleEl.checked = !!h.enabled;
    }
  };

  window.toggleHybrid = (enabled) => {
    fetch("/api/hybrid/toggle", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled}),
    });
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

  /* ------------------------------------------------ Activity Log (al-*) */
  const LOG_MAX = 250;
  const AL_PREF_CATS = ["mempool", "sweep", "competitor", "broadcast", "intel", "funds", "price", "bot"];
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

/* ------------------------------------------------ AI Manager workspace */
  let mgrReady = false;
  let mgrPollTimer = null;
  let mgrWinRateChart = null;
  let mgrProfitChart = null;
  let mgrEquityChart = null;

  const mgrApi = async (url, opts) => {
    const r = await fetch(url, opts);
    const txt = await r.text();
    let d = null;
    try { d = JSON.parse(txt); } catch (e) {
      if (!r.ok) {
        return { error: true, status: r.status, body: txt };
      }
    }
    return d;
  };

  const mgrSrvHint = (d) => {
    const el = $("mgr-srv-hint");
    if (!el) return;
    if (d && d.status === 404) {
      el.textContent = "AI Manager API not found — restart the dashboard (Stop, run dashboard.py again) so the new routes load.";
      el.classList.add("show");
    } else if (d && (d.error || d.status >= 400)) {
      el.textContent = `Server error ${d.status || ""}: ${String(d.body || d.message || "?").slice(0, 160)}`;
      el.classList.add("show");
    } else {
      el.classList.remove("show");
    }
  };

  const ensureManagerWorkspace = () => {
    if (mgrReady) { refreshManager(); return; }
    mgrReady = true;
    $("mgr-chat-send")?.addEventListener("click", mgrSendChat);
    $("mgr-chat-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") mgrSendChat();
    });
    refreshManager();
    mgrPollTimer = setInterval(refreshManager, 5000);
  };

  const refreshManager = async () => {
    const s = await mgrApi("/api/manager/status");
    const bi = await mgrApi("/api/manager/bi");
    if (s && !s.error) {
      renderMgrStatus(s);
      renderMgrApprovals(s.pending_approvals || []);
    }
    if (bi && !bi.error) {
      renderMgrBI(bi);
      renderMgrChanges(bi.changes_count || 0);
    }
    mgrSrvHint(s && s.error ? s : bi && bi.error ? bi : null);
  };

  const fmtNum = (v, d = 2) => (v == null || isNaN(v) ? "--" : Number(v).toFixed(d));
  const esc = (s) => String(s ?? "").replace(/</g, "&lt;").replace(/`/g, "&#96;").replace(/\$\{/g, "&#36;{");

  const renderMgrStatus = (s) => {
    const set = (id, v) => { const e = $(id); if (e) e.textContent = v; };
    set("mgr-model", s.model || s.error || "--");
    set("mgr-tools", s.tools_count ?? "--");
    set("mgr-skills", s.skills_count ?? "--");
    set("mgr-memory", s.memory_facts ?? "--");
    set("mgr-agents-q", s.agents_count ?? "--");
    set("mgr-pending", String((s.pending_approvals || []).length));
    const tag = $("mgr-status-tag");
    if (tag) tag.textContent = s.configured
      ? (s.pending_approvals?.length ? `${s.pending_approvals.length} need approval` : "operational")
      : "no API key in .env";
  };

  const renderMgrBI = (bi) => {
    const el = $("mgr-bi-metrics");
    if (!el) return;
    const sol = bi.sol_race || {}, eth = bi.eth_race || {}, cm = bi.competitors || {};
    const pf = bi.performance || {}, vit = bi.vitals || {}, f = bi.feeds || {};
    const sl = bi.sol_race_live || {}, elv = bi.eth_race_live || {};
    const models = bi.models || [];
    const win = (m) => m.win_rate == null ? "--" : (m.win_rate * 100).toFixed(0) + "%";
    const usd = (v, d = 0) => v == null || isNaN(v) ? "--" : "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
    const grade = pf.grade || "—";
    const vitals = [
      `${vit.chain || "?"} · block ${vit.block ?? "?"}`,
      `gas ${fmtNum(vit.gas_gwei)} gwei`,
      `ETH $${fmtNum(vit.eth_price_usd, 0)}`,
      vit.broadcast_ready ? "broadcast READY" : "broadcast off",
      vit.armed ? "armed" : vit.sim_only ? "sim-only" : "dry",
    ].join(" · ");
    el.innerHTML = `
      <div class="mgr-kpi-grid">
        <div class="mgr-bi-cell"><div class="mgr-bi-l">grade</div><div class="mgr-bi-v mgr-grade">${grade}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">session P&L</div><div class="mgr-bi-v ${(pf.session_pnl_usd||0)<0?"red":""}">${usd(pf.session_pnl_usd, 2)}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">realized</div><div class="mgr-bi-v">${usd(pf.realized_usd, 2)}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">missed to comps</div><div class="mgr-bi-v ${(pf.missed_comp_usd||0)>0?"red":""}">${usd(pf.missed_comp_usd, 2)}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">equity</div><div class="mgr-bi-v">${usd(pf.equity_usd, 2)}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">hit rate</div><div class="mgr-bi-v">${pf.hit_rate_pct == null ? "--" : pf.hit_rate_pct + "%"} <span class="mgr-bi-l">(${pf.wins ?? 0}/${pf.submits ?? 0})</span></div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">ETH win rate</div><div class="mgr-bi-v">${win(eth)}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">SOL win rate</div><div class="mgr-bi-v">${win(sol)}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">races</div><div class="mgr-bi-v">${(eth.total??0)+(sol.total??0)} <span class="mgr-bi-l">(${eth.total??0}E / ${sol.total??0}S)</span></div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">comps 1h</div><div class="mgr-bi-v">${cm.count_1h ?? 0}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">missed 1h</div><div class="mgr-bi-v ${(cm.missed_by_us||0)>0?"red":""}">${cm.missed_by_us ?? 0}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">best opp</div><div class="mgr-bi-v">${usd(pf.best_opp_usd)}</div></div>
      </div>
      <div class="mgr-verdict">${pf.grade ? "Verdict: " + esc(pf.verdict || "—") : ""}</div>
      <div class="mgr-vitals">${esc(vitals)}</div>
      ${ elv.inflight > 0 || sl.inflight > 0 ? `<div class="mgr-vitals live">races in flight — ETH ${elv.inflight} · SOL ${sl.inflight}</div>` : "" }
      <div class="mgr-models">
        ${models.map(m => `<div class="mgr-model chip">
          <b>${esc(m.name || "?")} brain</b>
          <span>${m.status === "unavailable" ? "untrained" : `dim ${m.feat_dim ?? "?"} · v${m.feat_version ?? 0} · ${m.steps ?? 0} steps`}</span>
          <span class="dim">${m.status === "unavailable" ? "no weights yet — learning when funded" : `loss ${m.loss_ema ?? "--"} · acc ${m.acc_ema ?? "--"}`}</span>
        </div>`).join("")}
      </div>
      <div class="mgr-live-strip">
        <span>sol race <b>${sl.mode ? "ON" : "OFF"}</b> · tuition ${sl.tuition_spent_sol ?? 0} / ${sl.tuition_day_sol ?? 0} SOL · tip cap ${sl.tip_cap_sol ?? 0} SOL · bandit mult ${fmtNum((sl.tip_stats||{}).mult)}</span>
        <span>eth bid mult ${fmtNum((elv.bid_stats||{}).mult)} · last winner ${esc(elv.last_winner || "—")}</span>
        <span>sol fee ${fmtNum(f.sol_fee_median === null ? 0 : f.sol_fee_median, 5)} median · tps ${fmtNum(f.sol_tps)} · comp ${f.sol_comp_1h ?? 0}/h</span>
      </div>`;
    renderMgrCharts(bi);
  };

  const renderMgrCharts = (bi) => {
    const render = (id, ref, cfg) => {
      const el = $(id);
      if (!el) return ref;
      if (ref) { ref.data = cfg.data; ref.options = cfg.options; ref.update("none"); return ref; }
      return new Chart(el, cfg);
    };
    const t = (txt) => ({ display: true, text: txt, color: "#94a3b8", font: { size: 11 } });
    mgrWinRateChart = render("mgr-chart-winrate", mgrWinRateChart, {
      type: "doughnut",
      data: {
        labels: ["won", "lost", "nc"],
        datasets: [{ data: [
          (bi.eth_race?.won||0)+(bi.sol_race?.won||0),
          (bi.eth_race?.lost||0)+(bi.sol_race?.lost||0),
          (bi.eth_race?.no_contest||0)+(bi.sol_race?.no_contest||0),
        ], backgroundColor: ["#10b981", "#ef4444", "#6b7280"], borderWidth: 0 }],
      },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { color: "#cbd5e1", boxWidth: 10, font: { size: 10 } } },
          title: t("Race outcomes (ETH+SOL)") } },
    });
    const pf = bi.performance || {};
    mgrProfitChart = render("mgr-chart-profit", mgrProfitChart, {
      type: "bar",
      data: {
        labels: ["Realized", "Simulated", "Missed to comps"],
        datasets: [{ label: "USD",
          data: [pf.realized_usd||0, pf.simulated_usd||0, pf.missed_comp_usd||0],
          backgroundColor: ["#10b981", "#a855f7", "#ef4444"], borderRadius: 4 }],
      },
      options: { responsive: true, maintainAspectRatio: false,
        scales: { x: { ticks: { color: "#94a3b8", font: { size: 10 } }, grid: { display: false } },
          y: { ticks: { color: "#94a3b8" }, grid: { color: "#1e293b" } } },
        plugins: { legend: { display: false }, title: t("Profit split (USD)") } },
    });
    const eh = (pf.equity_hist || []).map(p => p[1]);
    mgrEquityChart = render("mgr-chart-equity", mgrEquityChart, {
      type: "line",
      data: {
        labels: (pf.equity_hist || []).map((_, i) => i),
        datasets: [{ label: "equity",
          data: eh, borderColor: "#38bdf8", backgroundColor: "rgba(56,189,248,.12)",
          fill: true, tension: .25, pointRadius: 0, borderWidth: 2 }],
      },
      options: { responsive: true, maintainAspectRatio: false,
        scales: { x: { ticks: { display: false }, grid: { display: false } },
          y: { ticks: { color: "#94a3b8" }, grid: { color: "#1e293b" } } },
        plugins: { legend: { display: false }, title: t("Equity (USD)") } },
    });
  };

  const renderMgrChanges = (count) => {
    const el = $("mgr-bi-changes");
    if (!el) return;
    const c = $("mgr-bi-changes-count");
    if (c) c.textContent = String(count);
    el.textContent = count > 0 ? `changes journal: ${count} entries` : "no changes yet";
  };

  const mgrSendChat = async () => {
    const input = $("mgr-chat-input");
    const feed = $("mgr-chat-feed");
    if (!input || !feed) return;
    const msg = (input.value || "").trim();
    if (!msg) return;
    input.value = "";
    feed.innerHTML += `<div class="mgr-msg user"><span class="mgr-avatar you">You</span><span class="mgr-bubble">${esc(msg)}</span></div>`;
    feed.innerHTML += `<div class="mgr-msg system thinking" id="mgr-waiting"><span class="mgr-dot"></span><span class="mgr-dot"></span><span class="mgr-dot"></span></div>`;
    feed.scrollTop = feed.scrollHeight;
    const d = await mgrApi("/api/manager/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    $("mgr-waiting")?.remove();
    if (d && d.error) {
      feed.innerHTML += `<div class="mgr-msg error"><span class="mgr-avatar">!</span><span class="mgr-bubble">Server error (${d.status || "?"}). ${esc(d.body || "").slice(0, 200)}</span></div>`;
      mgrSrvHint(d);
      return;
    }
    const reply = (d && d.reply) || (d ? JSON.stringify(d).slice(0, 300) : "No reply.");
    feed.innerHTML += `<div class="mgr-msg assistant"><span class="mgr-avatar ai">AI</span><span class="mgr-bubble">${esc(reply)}</span></div>`;
    const tools = (d && d.transcript || []).filter(t => t.tool).map(t => t.tool);
    if (tools.length) {
      feed.innerHTML += `<div class="mgr-tools">used: ${tools.map(t => `<span class="mgr-tool-chip">${esc(t)}</span>`).join("")}</div>`;
    }
    if (d.applied_changes?.length) {
      feed.innerHTML += `<div class="mgr-msg approval"><span class="mgr-avatar">✓</span><span class="mgr-bubble">Applied: ${esc(d.applied_changes.join(", "))}</span></div>`;
    }
    if (d.needs_approval?.length) {
      feed.innerHTML += `<div class="mgr-msg approval"><span class="mgr-avatar">⚠</span><span class="mgr-bubble">Needs approval in "Pending Approvals": ${d.needs_approval.join(", ")}</span></div>`;
    }
    feed.scrollTop = feed.scrollHeight;
    refreshManager();
  };

  const renderMgrApprovals = (list) => {
    const el = $("mgr-approvals-list");
    if (!el) return;
    if (!list.length) { el.innerHTML = '<div class="mgr-approval-empty">nothing pending — all changes auto-applied unless they gate funds</div>'; return; }
    el.innerHTML = list.map(a => `
      <div class="mgr-approval-row" data-cid="${esc(a.id || "")}">
        <div class="mgr-approval-main">
          <div class="mgr-approval-target">${esc(a.target || "?")} = ${esc(a.new || "?")}</div>
          <div class="mgr-approval-reason">${esc(a.reason || a.message || "reason not given")}</div>
          ${a.prev != null ? `<div class="mgr-approval-prev">prev: ${esc(String(a.prev))}</div>` : ""}
        </div>
        <div class="mgr-approval-actions">
          <button class="mgr-approve-btn" onclick="mgrApprove('${esc(a.id)}',true)">Approve</button>
          <button class="mgr-reject-btn" onclick="mgrApprove('${esc(a.id)}',false)">Reject</button>
        </div>
      </div>
    `).join("");
  };

  window.mgrApprove = async (cid, approve) => {
    try {
      const d = await mgrApi("/api/manager/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ change_id: cid, approve }),
      });
      mgrSrvHint(d);
      refreshManager();
    } catch (_) {}
  };

  const clearMgrNotes = () => {
    if (mgrPollTimer) clearInterval(mgrPollTimer);
    mgrPollTimer = null;
  };

  /* ---------------- AI Manager popup briefing ---------------- */
  const openMgrPopup = () => {
    const ov = $("mgr-pop");
    if (ov) {
      ov.hidden = false;
      ov.classList.add("show");
    }
    $("mgr-pop-input")?.focus();
    refreshMgrPopup();
  };

  const closeMgrPopup = () => {
    const ov = $("mgr-pop");
    if (ov) {
      ov.classList.remove("show");
      ov.hidden = true;
    }
  };

  const renderMgrPopupBrief = (b) => {
    const body = $("mgr-pop-body");
    const tag = $("mgr-pop-tag");
    if (!body) return;
    $("mgr-pop-loading")?.remove();
    if (!b || b.error) {
      body.innerHTML = `<div class="mgr-msg error"><span class="mgr-avatar">!</span><span class="mgr-bubble">Briefing unavailable${b?.status ? ` (${b.status})` : ""} — ${esc(String(b?.body || b?.message || "?")).slice(0, 160)}${b?.status === 404 ? " Restart the dashboard so the briefing route loads." : ""}</span></div>`;
      if (tag) tag.textContent = "briefing failed";
      return;
    }
    if (tag) tag.textContent = `updated ${new Date(b.ts * 1000).toLocaleTimeString()} · ${b.verdict || "no verdict"}`;
    const m = b.metrics || {};
    const winS = m.win_rate_sol == null ? "--" : Math.round(m.win_rate_sol * 100) + "%";
    const usd = (v) => v == null ? "--" : "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const grade = b.grade || "—";
    body.innerHTML = `
      <div class="mgr-kpi-grid">
        <div class="mgr-bi-cell"><div class="mgr-bi-l">grade</div><div class="mgr-bi-v mgr-grade">${grade}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">equity</div><div class="mgr-bi-v">${usd(m.equity_usd)}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">realized</div><div class="mgr-bi-v">${usd(m.realized_usd)}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">SOL win rate</div><div class="mgr-bi-v">${winS}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">races</div><div class="mgr-bi-v">${m.races ?? 0}</div></div>
        <div class="mgr-bi-cell"><div class="mgr-bi-l">comps 1h</div><div class="mgr-bi-v">${m.comps_1h ?? 0}</div></div>
      </div>
      <div class="mgr-pop-sec"><div class="mgr-pop-sec-title">What's happening</div>
        ${(b.happening || []).map(t => `<div class="mgr-pop-line"><span class="mgr-pop-bullet">●</span>${esc(t)}</div>`).join("") || '<div class="mgr-pop-line"><span class="mgr-pop-bullet">●</span>quiet — nothing notable yet.</div>'}
      </div>
      <div class="mgr-pop-sec"><div class="mgr-pop-sec-title">What needs to be done</div>
        ${(b.todo || []).map((t, i) => `<div class="mgr-pop-line todo"><span class="mgr-pop-bullet">${i + 1}</span>${esc(t)}</div>`).join("") || '<div class="mgr-pop-line">all clear.</div>'}
      </div>`;
  };

  const refreshMgrPopup = async () => {
    const body = $("mgr-pop-body");
    if (!body) return;
    $("mgr-pop-loading")?.remove();
    body.insertAdjacentHTML("afterbegin", `<div class="mgr-pop-loading" id="mgr-pop-loading">loading briefing…</div>`);
    const d = await mgrApi("/api/manager/briefing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    $("mgr-pop-loading")?.remove();
    renderMgrPopupBrief(d);
  };

  const mgrPopSend = async () => {
    const input = $("mgr-pop-input");
    const popFeed = $("mgr-pop-feed");
    if (!input || !popFeed) return;
    const msg = (input.value || "").trim();
    if (!msg) return;
    input.value = "";
    popFeed.innerHTML += `<div class="mgr-msg user"><span class="mgr-avatar you">You</span><span class="mgr-bubble">${esc(msg)}</span></div>`;
    popFeed.innerHTML += `<div class="mgr-msg system thinking" id="mgr-pop-waiting"><span class="mgr-dot"></span><span class="mgr-dot"></span><span class="mgr-dot"></span></div>`;
    popFeed.scrollTop = popFeed.scrollHeight;
    const d = await mgrApi("/api/manager/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    $("mgr-pop-waiting")?.remove();
    if (d && d.error) {
      popFeed.innerHTML += `<div class="mgr-msg error"><span class="mgr-avatar">!</span><span class="mgr-bubble">Server error (${d.status || "?"}). ${esc(d.body || "").slice(0, 200)}</span></div>`;
      mgrSrvHint(d);
      return;
    }
    const reply = (d && d.reply) || "No reply.";
    popFeed.innerHTML += `<div class="mgr-msg assistant"><span class="mgr-avatar ai">AI</span><span class="mgr-bubble">${esc(reply)}</span></div>`;
    const tools = (d && d.transcript || []).filter(t => t.tool).map(t => t.tool);
    if (tools.length) {
      popFeed.innerHTML += `<div class="mgr-tools">used: ${tools.map(t => `<span class="mgr-tool-chip">${esc(t)}</span>`).join("")}</div>`;
    }
    if (d.applied_changes?.length) {
      popFeed.innerHTML += `<div class="mgr-msg approval"><span class="mgr-avatar">✓</span><span class="mgr-bubble">Applied: ${esc(d.applied_changes.join(", "))}</span></div>`;
    }
    if (d.needs_approval?.length) {
      popFeed.innerHTML += `<div class="mgr-msg approval"><span class="mgr-avatar">⚠</span><span class="mgr-bubble">Needs approval in "Pending Approvals": ${d.needs_approval.join(", ")}</span></div>`;
    }
    popFeed.scrollTop = popFeed.scrollHeight;
    const mainFeed = $("mgr-chat-feed");
    if (mainFeed) mainFeed.innerHTML += `<div class="mgr-msg user"><span class="mgr-avatar you">You</span><span class="mgr-bubble">${esc(msg)}</span></div><div class="mgr-msg assistant"><span class="mgr-avatar ai">AI</span><span class="mgr-bubble">${esc(reply)}</span></div>`;
    refreshManager();
  };

  document.addEventListener("click", (e) => {
    if (e.target && e.target.id === "mgr-pop") closeMgrPopup();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("mgr-pop") && !$("mgr-pop").hidden) closeMgrPopup();
  });
  $("mgr-pop-close")?.addEventListener("click", closeMgrPopup);
  $("mgr-pop-send")?.addEventListener("click", mgrPopSend);
  $("mgr-pop-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") mgrPopSend();
  });

  /* ------------------------------------------------ chain tabs ETH | SOL */
  const TAB_KEY = "toni-chain-tab";
  const CHAIN_TABS = ["eth", "sol", "manager"];
  const TAB_HINT = {
    eth: "Ethereum workspace · multi-protocol lending + Aave flash",
    sol: "Solana workspace · Solend liquidations + flash",
    manager: "AI manager · autonomous performance optimizer",
  };
  let activeTab = "eth";
  let solWorkspaceReady = false;

  const ensureSolWorkspace = () => {
    if (!solWorkspaceReady) {
      solWorkspaceReady = true;
      bindSolFilters();
      if (window.__lastState) renderSol(window.__lastState);
    }
    requestAnimationFrame(() => {
      try { solChartTx?.resize?.(); } catch (_) {}
      try { solChartComp?.resize?.(); } catch (_) {}
      resizeLiqCharts("sol");
    });
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
      chainLabel.textContent = activeTab === "eth" ? "ETH mainnet"
        : activeTab === "sol" ? "SOL mainnet" : "TONI AI";
    }

    if (activeTab === "sol") {
      ensureSolWorkspace();
      if (window.__lastState) renderSol(window.__lastState);
    } else if (activeTab === "manager") {
      ensureManagerWorkspace();
      openMgrPopup();
    } else {
      if (window.__lastState) {
        const s = window.__lastState;
        updateHeader(s);
        updateBots(s); updateFunds(s); updateMempool(s);
        updateOpps(s); updateCompetitors(s); updateLiqIntel(s); updateBroadcast(s); updateHybrid(s);
      }
      requestAnimationFrame(() => {
        try { chartTx?.resize?.(); } catch (_) {}
        try { chartComp?.resize?.(); } catch (_) {}
        resizeLiqCharts("eth");
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
    updateOpps(s); updateCompetitors(s); updateLiqIntel(s); updateBroadcast(s); updateHybrid(s);
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
