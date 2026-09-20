# SOL Feed "Collateral Seized" Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one "seized $" column to the SOL Opportunities feed table showing the USD value of collateral the bot would seize per opportunity.

**Architecture:** Pure front-end change. `seized_usd` already ships on every `sol.opportunities` row from all three scorers (Solend/Drift `sol_scanner.py:2430`, Kamino `kamino.py:223`, MarginFi `marginfi.py:350`). Add one `<th>` in `static/index.html` and one `<td>` in `renderSolOpps` in `static/app.js`.

**Tech Stack:** Static HTML + vanilla JS served by the aiohttp dashboard; no backend, no tests run.

## Global Constraints

- No backend or data-model change; reuse the existing `o.seized_usd` field as-is.
- Render `—` (dim) when `seized_usd` is null or ≤ 0.
- Column order becomes: `user | proto | hf | coll → debt | seized $ | bonus | net $ | flags`.
- Static files are served with ETag/Last-Modified and NO Cache-Control — browser verification REQUIRES a hard refresh (Ctrl+F5).

---
### Task 1: Add the "seized $" column to the SOL opportunities feed

**Files:**
- Modify: `static/index.html:846`
- Modify: `static/app.js:838` (row template inside `renderSolOpps`)
- Verify: `http://127.0.0.1:8081/api/state` → `sol.opportunities[*].seized_usd`

**Interfaces:**
- Consumes: `o.seized_usd` (float or null, already present on each opportunity row).
- Produces: rendered `<td>` "seized $" cell; expected row HTML has 8 `<td>`s.

- [ ] **Step 1: Add the header cell**

In `static/index.html`, change the `#sol-opps-table` header row at line 846 from:

```html
<tr>
  <th>user</th><th>proto</th><th>hf</th><th>coll → debt</th><th>bonus</th><th>net $</th><th>flags</th>
</tr>
```

to:

```html
<tr>
  <th>user</th><th>proto</th><th>hf</th><th>coll → debt</th><th title="collateral seized USD">seized $</th><th>bonus</th><th>net $</th><th>flags</th>
</tr>
```

- [ ] **Step 2: Before changing `renderSolOpps`, confirm the live API already carries the field**

Run:

```
python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8081/api/state')); ops=(d.get('sol') or {}).get('opportunities') or []; print(len(ops)); print([o.get('seized_usd') for o in ops[:3]])"
```

Expected: a list of numbers and/or `None` (e.g. `[0.0, None, 187.5]`). The field must exist (keys returned, even when `None`). If the API returns no rows at all, use a local row from `sol_scanner` sweep instead — the field is set at `sol_scanner.py:2430` regardless.

- [ ] **Step 3: Add the data cell in `renderSolOpps`**

In `static/app.js`, inside the row template at line 838, the `pair` cell ends with the `sizes` div, then line 839 begins the `bonus` cell:

```js
        <td><b>${pair}</b>${sizes}</td>
        <td style="color:var(--amber)">${o.liq_bonus_pct != null ? o.liq_bonus_pct + "%" : (o.bonus_usd != null ? fmt.usd(o.bonus_usd) : "--")}</td>
```

Insert the new cell between them:

```js
        <td><b>${pair}</b>${sizes}</td>
        <td title="${o.seized_usd != null ? "collateral seized USD" : ""}">${o.seized_usd != null && Number(o.seized_usd) > 0 ? fmt.usd(o.seized_usd) : `<span class="dim">—</span>`}</td>
        <td style="color:var(--amber)">${o.liq_bonus_pct != null ? o.liq_bonus_pct + "%" : (o.bonus_usd != null ? fmt.usd(o.bonus_usd) : "--")}</td>
```

Leave the existing `sizes` dim sub-line (`${fmt.usd(o.coll_usd)} / ${fmt.usd(o.debt_usd)} · repay ...`) untouched.

- [ ] **Step 4: Verify the served files are correct**

Run:

```
python -c "
import re
h = open('static/index.html', encoding='utf-8').read()
m = re.search(r'sol-opps-table[\s\S]{0,200}', h); print(m.group(0)[:220])
a = open('static/app.js', encoding='utf-8').read()
print('seized-usd cell present:', 'seized_usd != null && Number(o.seized_usd) > 0' in a)
"
```

Expected: header shows the new `seized $` `<th>`; second print is `True`. (Static files are read from disk per request — no dashboard restart needed.)

- [ ] **Step 5: Browser-verify both tabs (manual)**

Open `http://127.0.0.1:8081` in a browser and **hard-refresh (Ctrl+F5)** — no-cache headers are absent, stale JS will otherwise show old layout. Check the SOL opportunities table rows now have 8 columns and `seized $` shows `$X.XX` for sized opportunities and `—` (dim) for eligible rows with no repayable debt.

- [ ] **Step 6: Commit**

```bash
git add static/index.html static/app.js
git commit -m "feat: show seized collateral USD in SOL opportunities feed"
```

---