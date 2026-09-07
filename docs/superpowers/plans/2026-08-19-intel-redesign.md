# Learning / Intel Redesign — Liquidation Focus

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the Learning / Intel section into two side-by-side cards — Liquidation Intel (primary) and Trading Intel (secondary) — with new liq-specific data and charts.

**Architecture:** Extend the existing `intel_loop` pipeline with `liq_intel` fields. Replace the single `card-intel` HTML with two cards. Add `updateLiqIntel()` and `updateTradingIntel()` JS functions. Keep existing charts (hours, dows, act P trend) in the Trading Intel card.

**Tech Stack:** Python aiohttp, LightweightCharts, vanilla JS, CSS variables

## Global Constraints

- Dark theme: use CSS variables (`--line`, `--panel2`, `--cyan`, `--dim`, `--text`, `--green`, `--red`, `--amber`, `--violet`)
- Charts use LightweightCharts v4 (already in project)
- ETH and SOL tabs must have parallel structure (sol- prefixed IDs)
- All new DOM elements need null guards (`if (el)`)
- Dashboard runs on `http://127.0.0.1:8080`

---

### Task 1: Backend — Liq Intel Aggregation Functions

**Files:**
- Modify: `intel_collector.py`

**Interfaces:**
- Produces: `aggregate_liq_intel(spoke_rows, eth_price)` → `dict` with `liq_intel` shape

- [ ] **Step 1: Add `aggregate_liq_intel` function**

At the end of `intel_collector.py`, add:

```python
def aggregate_liq_intel(spoke_rows, eth_price=3500.0):
    """Aggregate liquidation intel from spoke transaction rows."""
    LIQ_SEL_PREFIXES = ("0xc2fa746c", "0xd8eabcb8")  # Aave liquidationCall, Morpho liquidate
    PROTO_MAP = {
        "Aave V3 Pool": "aave_v3",
        "Aave V4 Spoke": "aave_v3",
        "Compound cUSDCv3": "compound_v3",
        "Compound cWETHv3": "compound_v3",
        "Compound cUSDTv3": "compound_v3",
        "Morpho Blue": "morpho",
        "Spark Lend": "spark",
    }

    result = {
        "volume_24h": 0.0,
        "count_24h": 0,
        "avg_size": 0.0,
        "gas_per_liq": 0.0,
        "protocols": {
            "aave_v3": {"count": 0, "volume": 0.0},
            "compound_v3": {"count": 0, "volume": 0.0},
            "morpho": {"count": 0, "volume": 0.0},
            "spark": {"count": 0, "volume": 0.0},
        },
        "health_dist": {"<1.0": 0, "1.0-1.05": 0, "1.05-1.1": 0, ">1.1": 0},
        "competitors": {"searchers": 0, "success_rate": 0.0, "missed": 0},
        "volume_history": [],
    }

    liq_rows = [r for r in spoke_rows if r.get("sel") in LIQ_SEL_PREFIXES or r.get("name", "").startswith("liquidat")]
    if not liq_rows:
        return result

    result["count_24h"] = len(liq_rows)

    for row in liq_rows:
        proto_label = row.get("proto_label", "")
        proto_key = PROTO_MAP.get(proto_label, None)
        amount = float(row.get("amount", 0) or 0)

        if proto_key and proto_key in result["protocols"]:
            result["protocols"][proto_key]["count"] += 1
            result["protocols"][proto_key]["volume"] += amount

        result["volume_24h"] += amount

        hf = row.get("health_factor")
        if hf is not None:
            try:
                hf_f = float(hf)
                if hf_f < 1.0:
                    result["health_dist"]["<1.0"] += 1
                elif hf_f < 1.05:
                    result["health_dist"]["1.0-1.05"] += 1
                elif hf_f < 1.1:
                    result["health_dist"]["1.05-1.1"] += 1
                else:
                    result["health_dist"][">1.1"] += 1
            except (ValueError, TypeError):
                pass

    if result["count_24h"] > 0:
        result["avg_size"] = result["volume_24h"] / result["count_24h"]

    return result
```

- [ ] **Step 2: Commit**

```bash
git add intel_collector.py
git commit -m "feat: add aggregate_liq_intel function to intel_collector"
```

---

### Task 2: Backend — Extend intel_loop with liq_intel

**Files:**
- Modify: `dashboard.py` (intel_loop function, ~line 3009)

**Interfaces:**
- Consumes: `aggregate_liq_intel` from Task 1
- Produces: `state["intel"]["liq_intel"]` dict in snapshot

- [ ] **Step 1: Import aggregate_liq_intel**

At the top of `dashboard.py`, ensure the import exists (near other intel_collector imports):

```python
from intel_collector import aggregate_liq_intel
```

- [ ] **Step 2: Initialize liq_intel in state**

In the ETH state initialization (line ~738), add to the `intel` dict:

```python
"liq_intel": {
    "volume_24h": 0.0, "count_24h": 0, "avg_size": 0.0, "gas_per_liq": 0.0,
    "protocols": {"aave_v3": {"count": 0, "volume": 0.0}, "compound_v3": {"count": 0, "volume": 0.0},
                  "morpho": {"count": 0, "volume": 0.0}, "spark": {"count": 0, "volume": 0.0}},
    "health_dist": {"<1.0": 0, "1.0-1.05": 0, "1.05-1.1": 0, ">1.1": 0},
    "competitors": {"searchers": 0, "success_rate": 0.0, "missed": 0},
    "volume_history": [],
},
```

- [ ] **Step 3: Compute liq_intel in intel_loop**

Inside `intel_loop()`, after the existing spoke_txs collection and before the state update, add:

```python
# --- liquidation intel aggregation ---
eth_price = self.state.get("eth_price", 3500.0)
liq_data = aggregate_liq_intel(spoke_rows, eth_price=eth_price)

# append to volume history (rolling 24h = max 288 entries at 75s intervals)
vol_hist = self.state["intel"].get("liq_intel", {}).get("volume_history", [])
vol_hist.append({"ts": int(time.time()), "volume": liq_data["volume_24h"]})
if len(vol_hist) > 288:
    vol_hist = vol_hist[-288:]
liq_data["volume_history"] = vol_hist

self.state["intel"]["liq_intel"] = liq_data
```

- [ ] **Step 4: Add liq_intel to SOL intel state**

In the SOL state initialization (line ~921), add the same `liq_intel` shape to `sol["intel"]`.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat: extend intel_loop with liq_intel aggregation"
```

---

### Task 3: Frontend HTML — Split Intel Card into Two

**Files:**
- Modify: `static/index.html` (ETH card-intel section ~line 471, SOL card-intel ~line 1117)

**Interfaces:**
- Consumes: existing element IDs (keep them, just reorganize)
- Produces: new element IDs for liq_intel elements

- [ ] **Step 1: Replace ETH card-intel with two side-by-side cards**

Replace the single `card-intel` div with two `span-3` cards:

```html
<!-- Liquidation Intel -->
<div class="card span-3" id="card-liq-intel">
  <div class="card-head">Liquidation Intel <span id="in-pressure" class="tag"></span></div>
  <div class="liq-hero">
    <div class="liq-hero-stat"><span class="liq-hero-label">Volume 24h</span><span class="liq-hero-val" id="liq-volume">$0</span></div>
    <div class="liq-hero-stat"><span class="liq-hero-label">Liq Count</span><span class="liq-hero-val" id="liq-count">0</span></div>
    <div class="liq-hero-stat"><span class="liq-hero-label">Avg Size</span><span class="liq-hero-val" id="liq-avg">$0</span></div>
    <div class="liq-hero-stat"><span class="liq-hero-label">Gas/Liq</span><span class="liq-hero-val" id="liq-gas">$0</span></div>
  </div>
  <div class="liq-protocols">
    <div class="liq-proto-bar" id="liq-proto-bar"></div>
    <div class="liq-proto-labels" id="liq-proto-labels"></div>
  </div>
  <div class="liq-section-head">Health Factor Distribution</div>
  <div id="liq-health-chart" style="height:120px"></div>
  <div class="liq-competitors">
    <div class="liq-comp-stat"><span class="liq-comp-label">Searchers</span><span id="liq-comp-searchers">0</span></div>
    <div class="liq-comp-stat"><span class="liq-comp-label">Success Rate</span><span id="liq-comp-rate">0%</span></div>
    <div class="liq-comp-stat"><span class="liq-comp-label">Missed</span><span id="liq-comp-missed">0</span></div>
  </div>
  <div class="liq-section-head">Volume History (24h)</div>
  <div id="liq-volume-chart" style="height:120px"></div>
</div>

<!-- Trading Intel -->
<div class="card span-3" id="card-intel">
  <div class="card-head">Trading Intel</div>
  <div class="intel-hero">
    <div class="intel-hero-stat"><span class="intel-hero-label">Act P</span><span class="intel-hero-val" id="intel-act">—</span></div>
    <div class="intel-hero-stat"><span class="intel-hero-label">Exp Net</span><span class="intel-hero-val" id="intel-exp">—</span></div>
    <div class="intel-hero-stat"><span class="intel-hero-label">Steps</span><span class="intel-hero-val" id="intel-steps">0</span></div>
    <div class="intel-hero-stat"><span class="intel-hero-label">Records</span><span class="intel-hero-val" id="intel-records">0</span></div>
  </div>
  <div id="in-advice" class="intel-advice">warming up</div>
  <div id="in-meta" class="intel-meta"></div>
  <div class="intel-gauge-wrap">
    <canvas id="gauge"></canvas>
    <span id="in-ready-pct" class="intel-ready-pct">0</span>
  </div>
  <div class="intel-mix-track" id="in-mix-track"></div>
  <div id="intel-mev" class="intel-mev"></div>
  <div class="intel-brain-panel" id="intel-brain"></div>
  <div class="intel-section-head">Hours Activity</div>
  <div id="chart-hours" style="height:100px"></div>
  <div class="intel-section-head">Weekday Activity</div>
  <div id="chart-dows" style="height:100px"></div>
  <div class="intel-section-head">Act P Trend</div>
  <div id="chart-intel-trend" style="height:100px"></div>
</div>
```

- [ ] **Step 2: Replace SOL card-intel with two side-by-side cards**

Same structure as ETH but with `sol-` prefixed IDs. Keep all existing `sol-` element IDs intact. Add new `sol-liq-*` elements.

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat: split intel card into liq-intel and trading-intel cards"
```

---

### Task 4: Frontend JS — Liq Intel Rendering + Charts

**Files:**
- Modify: `static/app.js`

**Interfaces:**
- Consumes: `s.intel.liq_intel` from snapshot
- Produces: `updateLiqIntel(s)` function, chart initialization

- [ ] **Step 1: Initialize liq health and volume charts**

In the chart initialization section (near line 110), add:

```javascript
let liqHealthChart = null;
let liqVolumeChart = null;

function initLiqCharts() {
  const healthEl = $("liq-health-chart");
  if (healthEl) {
    liqHealthChart = LightweightCharts.createBarChart(healthEl, {
      layout: { background: { color: "transparent" }, textColor: "#8b8fa3" },
      grid: { vertLines: { color: "rgba(42,46,64,.4)" }, horzLines: { color: "rgba(42,46,64,.4)" } },
      rightPriceScale: { visible: false },
      timeScale: { visible: false, rightOffset: 0 },
      crosshair: { mode: 0 },
    });
    liqHealthChart.applyOptions({ width: healthEl.clientWidth });
  }

  const volEl = $("liq-volume-chart");
  if (volEl) {
    liqVolumeChart = LightweightCharts.createLineChart(volEl, {
      layout: { background: { color: "transparent" }, textColor: "#8b8fa3" },
      grid: { vertLines: { color: "rgba(42,46,64,.4)" }, horzLines: { color: "rgba(42,46,64,.4)" } },
      rightPriceScale: { visible: false },
      timeScale: { visible: false, rightOffset: 0 },
      crosshair: { mode: 0 },
    });
    liqVolumeChart.applyOptions({ width: volEl.clientWidth });
  }
}
```

Call `initLiqCharts()` after existing chart inits.

- [ ] **Step 2: Add `updateLiqIntel(s)` function**

```javascript
function updateLiqIntel(s) {
  const li = s.intel && s.intel.liq_intel;
  if (!li) return;

  // Hero stats
  const vol = li.volume_24h || 0;
  const el = (id) => document.getElementById(id);
  const fmtK = (v) => v >= 1000 ? "$" + (v / 1000).toFixed(1) + "k" : "$" + v.toFixed(0);
  const fmtD = (v) => "$" + v.toFixed(2);

  const volEl = el("liq-volume"); if (volEl) volEl.textContent = fmtK(vol);
  const cntEl = el("liq-count"); if (cntEl) cntEl.textContent = li.count_24h || 0;
  const avgEl = el("liq-avg"); if (avgEl) avgEl.textContent = fmtK(li.avg_size || 0);
  const gasEl = el("liq-gas"); if (gasEl) gasEl.textContent = fmtD(li.gas_per_liq || 0);

  // Protocol breakdown bar
  const protoBar = el("liq-proto-bar");
  const protoLabels = el("liq-proto-labels");
  if (protoBar && li.protocols) {
    const total = Object.values(li.protocols).reduce((s, p) => s + p.count, 0) || 1;
    const colors = { aave_v3: "#22d3ee", compound_v3: "#22c55e", morpho: "#a78bfa", spark: "#f59e0b" };
    const names = { aave_v3: "Aave", compound_v3: "Compound", morpho: "Morpho", spark: "Spark" };
    let barHtml = "";
    let labelHtml = "";
    for (const [k, v] of Object.entries(li.protocols)) {
      const pct = (v.count / total * 100).toFixed(1);
      barHtml += `<div style="width:${pct}%;background:${colors[k] || '#666'}"></div>`;
      labelHtml += `<span style="color:${colors[k]}">${names[k]} ${v.count}</span>`;
    }
    protoBar.innerHTML = barHtml;
    if (protoLabels) protoLabels.innerHTML = labelHtml;
  }

  // Health distribution chart
  if (liqHealthChart && li.health_dist) {
    const hd = li.health_dist;
    const time = Math.floor(Date.now() / 1000);
    liqHealthChart.series().setData([
      { time: time - 3, value: hd["<1.0"] || 0, color: "#ef4444" },
      { time: time - 2, value: hd["1.0-1.05"] || 0, color: "#f59e0b" },
      { time: time - 1, value: hd["1.05-1.1"] || 0, color: "#22c55e" },
      { time: time, value: hd[">1.1"] || 0, color: "#6b7280" },
    ]);
  }

  // Competitor stats
  const comp = li.competitors || {};
  const sEl = el("liq-comp-searchers"); if (sEl) sEl.textContent = comp.searchers || 0;
  const rEl = el("liq-comp-rate"); if (rEl) rEl.textContent = ((comp.success_rate || 0) * 100).toFixed(0) + "%";
  const mEl = el("liq-comp-missed"); if (mEl) mEl.textContent = comp.missed || 0;

  // Volume history chart
  if (liqVolumeChart && li.volume_history && li.volume_history.length) {
    const series = li.volume_history.map(h => ({ time: h.ts, value: h.volume }));
    liqVolumeChart.series().setData(series);
  }

  // Pressure badge (reuse existing)
  const ready = s.intel ? (s.intel.readiness || 0) : 0;
  const pBadge = el("in-pressure");
  if (pBadge) {
    const pr = ready >= 50 ? "hot" : ready >= 25 ? "busy" : ready >= 8 ? "quiet" : "idle";
    pBadge.textContent = pr;
    pBadge.className = "tag " + pr;
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add static/app.js
git commit -m "feat: add updateLiqIntel rendering and liq charts"
```

---

### Task 5: Frontend JS — Trading Intel + Wire into Render

**Files:**
- Modify: `static/app.js`

**Interfaces:**
- Consumes: existing `updateIntel(s)` logic, `s.paper_eth`/`s.paper_sol`
- Produces: `updateTradingIntel(s)` function, calls from `render()` and `renderSol()`

- [ ] **Step 1: Rename `updateIntel` to `updateTradingIntel`**

Rename the function at line ~2629 from `updateIntel` to `updateTradingIntel`. Keep all internal logic the same (brain knobs, hours chart, dows chart, act P trend).

- [ ] **Step 2: Add paper trading performance subsection**

At the end of `updateTradingIntel`, add:

```javascript
// Paper trading performance (reuses paper panel data)
// Already handled by renderPaperPanel — no duplicate needed
```

The paper trading performance is already rendered by `renderPaperPanel()` which is called from `render()`. No additional code needed — it's already in the Trading Intel card area.

- [ ] **Step 3: Update render() and renderSol() calls**

In `render()` (line ~3826), change:
```javascript
updateIntel(s);        // old
updateTradingIntel(s); // new
updateLiqIntel(s);     // new
```

In `renderSol()` (line ~1459), add:
```javascript
updateLiqIntel(s);  // SOL liq intel
```

- [ ] **Step 4: Initialize SOL liq charts**

Add SOL-specific liq chart variables and init them in `initLiqCharts()`:
```javascript
let solLiqHealthChart = null;
let solLiqVolumeChart = null;
```

Add SOL chart creation inside `initLiqCharts()` with `sol-liq-health-chart` and `sol-liq-volume-chart` element IDs.

Update `updateLiqIntel` to accept a prefix parameter or detect SOL context and use the correct chart variables.

- [ ] **Step 5: Commit**

```bash
git add static/app.js
git commit -m "feat: wire updateLiqIntel and updateTradingIntel into render loop"
```

---

### Task 6: Frontend CSS — Liq Intel Styles

**Files:**
- Modify: `static/style.css`

**Interfaces:**
- Consumes: classes from Task 3 HTML
- Produces: styled liq intel elements

- [ ] **Step 1: Add liq intel CSS rules**

Append to `static/style.css`:

```css
/* --- Liquidation Intel card --- */
.liq-hero { display: flex; gap: 12px; margin-bottom: 10px; }
.liq-hero-stat {
  flex: 1; padding: 8px 10px; border-radius: 8px;
  background: rgba(34,211,238,.05); border: 1px solid var(--line);
  display: flex; flex-direction: column; align-items: center;
}
.liq-hero-label { font-size: 9px; text-transform: uppercase; letter-spacing: .5px; color: var(--dim); }
.liq-hero-val { font-size: 16px; font-weight: 700; color: var(--text); font-family: var(--mono); margin-top: 2px; }
.liq-protocols { margin-bottom: 10px; }
.liq-proto-bar {
  display: flex; height: 8px; border-radius: 4px; overflow: hidden;
  background: var(--line); margin-bottom: 4px;
}
.liq-proto-bar > div { height: 100%; }
.liq-proto-labels { display: flex; gap: 10px; font-size: 10px; font-family: var(--mono); }
.liq-section-head {
  font-size: 10px; text-transform: uppercase; letter-spacing: .5px;
  color: var(--dim); margin: 8px 0 4px; border-top: 1px solid var(--line); padding-top: 6px;
}
.liq-competitors { display: flex; gap: 12px; margin: 6px 0; }
.liq-comp-stat { display: flex; flex-direction: column; align-items: center; flex: 1; }
.liq-comp-label { font-size: 9px; text-transform: uppercase; color: var(--dim); }
.liq-comp-stat span:last-child { font-size: 14px; font-weight: 600; color: var(--text); font-family: var(--mono); }
```

- [ ] **Step 2: Commit**

```bash
git add static/style.css
git commit -m "feat: add liquidation intel card styles"
```

---

### Task 7: Integration Test

**Files:**
- All files from Tasks 1-6

**Interfaces:**
- Full system test — end-to-end liq intel rendering

- [ ] **Step 1: Restart dashboard and verify**

```bash
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Start-Process python -ArgumentList "dashboard.py --broadcast" -WindowStyle Hidden
Start-Sleep 20
```

- [ ] **Step 2: Verify backend state**

```bash
curl -s http://127.0.0.1:8080/api/state | python -c "
import sys, json
d = json.load(sys.stdin)
li = d.get('intel', {}).get('liq_intel', {})
print('liq_intel present:', bool(li))
print('volume_24h:', li.get('volume_24h'))
print('count_24h:', li.get('count_24h'))
print('protocols:', li.get('protocols'))
print('health_dist:', li.get('health_dist'))
print('competitors:', li.get('competitors'))
"
```

- [ ] **Step 3: Verify frontend HTML**

Open `http://127.0.0.1:8080` in browser:
- Two side-by-side cards visible (Liquidation Intel + Trading Intel)
- Liq hero stats show values
- Protocol breakdown bar renders
- Health dist chart renders
- Competitor stats show values
- Volume history chart renders
- Trading Intel card has brain panel, hours/dow charts, act P trend
- No console errors

- [ ] **Step 4: Final commit if fixes needed**

```bash
git add -A
git commit -m "fix: liq intel integration adjustments"
```
