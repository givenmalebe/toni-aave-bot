"""AI Manager — autonomous profit optimizer with tool registry, skill authoring,
agent spawning, change journaling, and OpenRouter-powered chat.

Design choices:
- Model calls use plain JSON contracts (no function-calling dependency):
  {"action":"tool_name","args":{...}} or {"reply":"..."}.
- Fund-gating knobs require human approval before applying.
- All mutations journaled and reversible.
- Bounded agents run in-process with step/token caps.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_HERE, "data")
_AI_TOOLS_DIR = os.path.join(_DATA_DIR, "ai_tools")
_AI_SKILLS_DIR = os.path.join(_DATA_DIR, "ai_skills")
_MANAGER_STATE_PATH = os.path.join(_DATA_DIR, "manager_state.json")
_CHANGE_JOURNAL = os.path.join(_DATA_DIR, "manager_change_journal.jsonl")
_ENV_PATH = os.path.join(_HERE, ".env")
_MODEL = os.environ.get("AI_MANAGER_MODEL", "minimax/minimax-m3:free")
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _load_env_if_missing() -> None:
    """Bootstrap .env into os.environ (only when the var is unset) so the
    manager works regardless of import order or host app."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    try:
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    k, _, v = s.partition("=")
                    k = k.strip()
                    if k and k not in os.environ:
                        os.environ[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass


_load_env_if_missing()
_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
_MAX_TOOL_ROUNDS = 8
_MAX_THREAD_MSGS = 40
_MAX_MEMORY_FACTS = 30

# Fund-gating knobs — require human approval before any automated change.
FUND_KNOBS: set = {
    "SOL_RACE_MODE", "SOL_RACE_TUITION_DAY_SOL", "SOL_RACE_TIP_CAP_SOL",
    "SOL_RACE_MAX_FIRES_PER_CYCLE", "SOL_CONTESTED_SLOTS",
    "MIN_PROFIT_USD", "MIN_PROFIT_USD_SOL",
    "BROADCAST", "KEEP_LIVE", "SOL_KEEP_LIVE",
    "ARMED", "SIM_ONLY",
    "ETH_MAX_GAS_COST_ETH", "ETH_MIN_GAS_ETH",
    "SOL_MAX_TIP_SOL",
    "LIQ_CONTRACT", "GENERIC_LIQ",
}

# Only safe commands allowed via run_command (tests + read-only inspection).
_ALLOWED_CMD_RES = [
    re.compile(r"^python -m pytest\b"),
    re.compile(r"^python -c\b"),
    re.compile(r"^rg\b"),
    re.compile(r"^cat\b"),
    re.compile(r"^ls\b"),
    re.compile(r"^type\b"),
    re.compile(r"^Get-Content\b"),
    re.compile(r"^dir\b"),
    re.compile(r"^Select-String\b"),
    re.compile(r"^head\b"),
    re.compile(r"^tail\b"),
    re.compile(r"^wc\b"),
]

_SYSTEM_PROMPT = """You are the TONI AI Manager — an autonomous DeFi liquidation bot performance optimizer running on Ethereum + Solana.

Your mission: maximize profitability and model performance by analyzing live data, making bounded config changes, authoring new tools and skills for your minions, and recommending architecture improvements.

You have access to live dashboard data, config, competitors, model health, and profit/loss ledgers.

OUTPUT FORMAT (always exactly one JSON object, nothing else):
- To use a tool: {"action":"tool_name","args":{...}}
- To reply to the user: {"reply":"your message"}

RULES:
1. Always output exactly one valid JSON object.
2. Never modify fund-gating knobs without noting it requires human approval.
3. Keep changes minimal and explain your reasoning in the reply after any tool call.
4. Use create_tool/create_skill to persist reusable knowledge for your agents.
5. Use spawn_agent to decompose complex analysis into sub-tasks.
6. If uncertain, ask the user via a reply.
7. Read data before proposing changes.
"""

# ---------------------------------------------------------------------------
# Tool / Skill dataclasses
# ---------------------------------------------------------------------------

class ToolDef:
    __slots__ = ("name", "desc", "params", "handler", "created_by", "path")

    def __init__(self, name: str, desc: str, params: dict,
                 handler: Callable[[Dict[str, Any]], Any],
                 created_by: str = "system", path: str = ""):
        self.name = name
        self.desc = desc
        self.params = params
        self.handler = handler
        self.created_by = created_by
        self.path = path

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.desc,
            "parameters": self.params or {"type": "object", "properties": {}},
        }


class SkillDef:
    __slots__ = ("name", "desc", "steps", "path")

    def __init__(self, name: str, desc: str, steps: List[dict], path: str = ""):
        self.name = name
        self.desc = desc
        self.steps = steps
        self.path = path


# ---------------------------------------------------------------------------
# Journal entry
# ---------------------------------------------------------------------------

class ChangeEntry:
    __slots__ = ("id", "ts", "target", "prev", "new", "change_type",
                 "reason", "needs_approval", "approved", "reverted", "applied")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "ChangeEntry":
        return cls(**{k: d.get(k) for k in cls.__slots__})


# ---------------------------------------------------------------------------
# AIManager
# ---------------------------------------------------------------------------

class AIManager:
    """Orchestrator: LLM chat, tools, agents, skills, changes, BI."""

    def __init__(self, snapshot_fn: Callable[[], dict]):
        self._snapshot = snapshot_fn
        self._lock = threading.RLock()
        os.makedirs(_DATA_DIR, exist_ok=True)
        os.makedirs(_AI_TOOLS_DIR, exist_ok=True)
        os.makedirs(_AI_SKILLS_DIR, exist_ok=True)
        self._state = self._load_state()
        self._tools: Dict[str, ToolDef] = {}
        self._skills: Dict[str, SkillDef] = {}
        self._register_builtins()
        self._load_custom_tools()
        self._load_custom_skills()
        self._journal: List[ChangeEntry] = self._load_journal()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        try:
            with open(_MANAGER_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"threads": {}, "memory": [], "agents": []}

    def _save_state(self) -> None:
        try:
            with open(_MANAGER_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
        except Exception:
            pass

    def _load_journal(self) -> List[ChangeEntry]:
        entries: List[ChangeEntry] = []
        try:
            with open(_CHANGE_JOURNAL, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(ChangeEntry.from_dict(json.loads(line)))
        except FileNotFoundError:
            pass
        return entries

    def _append_journal(self, entry: ChangeEntry) -> None:
        with self._lock:
            self._journal.append(entry)
            try:
                with open(_CHANGE_JOURNAL, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry.to_dict(), default=str) + "\n")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Tool / Skill registry
    # ------------------------------------------------------------------

    def _register_builtins(self) -> None:
        builtins = [
            ToolDef("get_snapshot", "Return the live dashboard snapshot (compact).",
                    {"type": "object", "properties": {"compact": {"type": "boolean"}}},
                    self._tool_get_snapshot),
            ToolDef("get_bi", "Return Power BI metrics: profit, win rates, model health, competitors.",
                    {"type": "object", "properties": {}},
                    self._tool_get_bi),
            ToolDef("list_tools", "List all registered tools.",
                    {"type": "object", "properties": {}},
                    self._tool_list_tools),
            ToolDef("list_skills", "List all registered skills.",
                    {"type": "object", "properties": {}},
                    self._tool_list_skills),
            ToolDef("read_config", "Read .env config. Optionally filter by key list.",
                    {"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}}}},
                    self._tool_read_config),
            ToolDef("apply_change", "Apply a config change (env key=value). Fund-knob changes need approval.",
                    {"type": "object", "properties": {
                        "target": {"type": "string"},
                        "value": {"type": "string"},
                        "reason": {"type": "string"},
                    }, "required": ["target", "value"]},
                    self._tool_apply_change),
            ToolDef("revert_change", "Revert a previous change by change_id.",
                    {"type": "object", "properties": {"change_id": {"type": "string"}}, "required": ["change_id"]},
                    self._tool_revert_change),
            ToolDef("list_changes", "List recent change journal entries.",
                    {"type": "object", "properties": {"limit": {"type": "integer"}}},
                    self._tool_list_changes),
            ToolDef("create_tool", "Persist a new reusable tool (JSON in data/ai_tools/).",
                    {"type": "object", "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "command": {"type": "string"},
                        "param_schema": {"type": "object"},
                    }, "required": ["name", "description", "command"]},
                    self._tool_create_tool),
            ToolDef("create_skill", "Persist a new skill (JSON in data/ai_skills/).",
                    {"type": "object", "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "steps": {"type": "array", "items": {"type": "object"}},
                    }, "required": ["name", "description", "steps"]},
                    self._tool_create_skill),
            ToolDef("run_command", "Run a safe shell command (allowlisted). Returns stdout/stderr.",
                    {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
                    self._tool_run_command),
            ToolDef("spawn_agent", "Spawn a bounded sub-agent with a goal and allowed tools.",
                    {"type": "object", "properties": {
                        "goal": {"type": "string"},
                        "allowed_tools": {"type": "array", "items": {"type": "string"}},
                        "budget_steps": {"type": "integer"},
                        "context": {"type": "string"},
                    }, "required": ["goal"]},
                    self._tool_spawn_agent),
            ToolDef("read_file", "Read a file (path relative to repo root).",
                    {"type": "object", "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer"},
                    }, "required": ["path"]},
                    self._tool_read_file),
            ToolDef("add_memory", "Add a memory fact for the manager.",
                    {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
                    self._tool_add_memory),
        ]
        for t in builtins:
            self._tools[t.name] = t

    def _load_custom_tools(self) -> None:
        if not os.path.isdir(_AI_TOOLS_DIR):
            return
        for fn in os.listdir(_AI_TOOLS_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(_AI_TOOLS_DIR, fn), "r", encoding="utf-8") as f:
                    d = json.load(f)
                cmd = d.get("command", "")
                if not self._cmd_allowed(cmd):
                    continue
                name = d["name"]
                td = ToolDef(
                    name=name,
                    desc=d.get("description", ""),
                    params=d.get("param_schema", {"type": "object", "properties": {}}),
                    handler=self._make_command_handler(cmd),
                    created_by="manager",
                    path=os.path.join(_AI_TOOLS_DIR, fn),
                )
                self._tools[name] = td
            except Exception:
                pass

    def _load_custom_skills(self) -> None:
        if not os.path.isdir(_AI_SKILLS_DIR):
            return
        for fn in os.listdir(_AI_SKILLS_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(_AI_SKILLS_DIR, fn), "r", encoding="utf-8") as f:
                    d = json.load(f)
                self._skills[d["name"]] = SkillDef(
                    name=d["name"],
                    desc=d.get("description", ""),
                    steps=d.get("steps", []),
                    path=os.path.join(_AI_SKILLS_DIR, fn),
                )
            except Exception:
                pass

    def _register_tool(self, td: ToolDef) -> None:
        self._tools[td.name] = td
        self._state.setdefault("tools_created", [])
        if td.path:
            self._state["tools_created"].append(td.name)
        self._save_state()

    def _register_skill(self, sd: SkillDef) -> None:
        self._skills[sd.name] = sd
        self._save_state()

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def _call_llm(self, messages: List[dict], temperature: float = 0.4) -> str:
        if not _API_KEY:
            return json.dumps({"reply": "OPENROUTER_API_KEY not set. Please add it to .env and restart."})
        try:
            import urllib.request
            body = json.dumps({
                "model": _MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 1024,
            }).encode()
            req = urllib.request.Request(
                _OPENROUTER_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/toni-aave-bot",
                    "X-Title": "TONI AI Manager",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            return json.dumps({"reply": f"LLM call failed: {e}"})

    # ------------------------------------------------------------------
    # Orchestrator loop (LLM ↔ tools)
    # ------------------------------------------------------------------

    def _run_orchestrator(self, user_msg: str, thread_id: str = "default") -> dict:
        thread = self._state.setdefault("threads", {}).setdefault(thread_id, [])
        memory = self._state.get("memory", [])

        # Build context
        snap = {}
        try:
            snap = self._snapshot()
        except Exception:
            snap = {"error": "snapshot unavailable"}

        # Compact snapshot: strip large feed/log fields
        compact = {k: v for k, v in (snap or {}).items()
                   if k not in ("recent_logs", "feed", "log_meta", "liq_intel")}

        context_msg = (
            f"LIVE SNAPSHOT (compact): {json.dumps(compact, default=str)[:3000]}\n"
            f"MANAGER MEMORY: {json.dumps(memory[-10:])}\n"
            f"AVAILABLE TOOLS: {', '.join(self._tools.keys())}\n"
        )

        msgs: List[dict] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "system", "content": context_msg},
        ]

        # Thread history (last 12 messages)
        for m in thread[-12:]:
            msgs.append({"role": m["role"], "content": m["content"]})

        msgs.append({"role": "user", "content": user_msg})
        thread.append({"role": "user", "content": user_msg, "ts": time.time()})

        transcript: List[dict] = []
        applied: List[str] = []
        needs_approval: List[str] = []

        for _round in range(_MAX_TOOL_ROUNDS):
            raw = self._call_llm(msgs)
            parsed = self._parse_response(raw)
            transcript.append({"round": len(transcript) + 1, "raw": raw[:500]})

            action = parsed.get("action")
            if action:
                args = parsed.get("args", {})
                result = self._exec_tool(action, args)
                result_str = json.dumps(result, default=str)[:2000]
                transcript.append({"tool": action, "args": args, "result": result_str[:300]})
                msgs.append({"role": "assistant", "content": raw})
                msgs.append({"role": "user", "content": (
                    f"Tool result for {action}:\n{result_str}\n"
                    f"Continue with another tool call or a reply to the user."
                )})

                # Track approvals
                if action == "apply_change":
                    r = result if isinstance(result, dict) else {}
                    if r.get("needs_approval"):
                        needs_approval.append(r.get("change_id", action))
                    elif r.get("applied"):
                        applied.append(r.get("change_id", action))
            else:
                reply = parsed.get("reply", raw)
                thread.append({"role": "assistant", "content": reply, "ts": time.time()})
                # Trim thread
                while len(thread) > _MAX_THREAD_MSGS:
                    thread.pop(0)
                self._save_state()
                return {
                    "reply": reply,
                    "transcript": transcript,
                    "applied_changes": applied,
                    "needs_approval": needs_approval,
                    "thread_id": thread_id,
                }

        # Exhausted rounds — force reply
        last_reply = parsed.get("reply", "I've completed my analysis. Check the transcript for tool results.")
        thread.append({"role": "assistant", "content": last_reply, "ts": time.time()})
        while len(thread) > _MAX_THREAD_MSGS:
            thread.pop(0)
        self._save_state()
        return {
            "reply": last_reply,
            "transcript": transcript,
            "applied_changes": applied,
            "needs_approval": needs_approval,
            "thread_id": thread_id,
        }

    @staticmethod
    def _parse_response(text: str) -> dict:
        text = text.strip()
        # Try direct JSON
        try:
            return json.loads(text)
        except Exception:
            pass
        # Extract first JSON object from text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        return {"reply": text}

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _exec_tool(self, name: str, args: dict) -> Any:
        td = self._tools.get(name)
        if not td:
            return {"error": f"unknown tool: {name}"}
        try:
            return td.handler(args)
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Built-in tool handlers
    # ------------------------------------------------------------------

    def _tool_get_snapshot(self, args: dict) -> dict:
        snap = {}
        try:
            snap = self._snapshot()
        except Exception:
            return {"error": "snapshot unavailable"}
        if args.get("compact"):
            return {k: v for k, v in snap.items()
                    if k not in ("recent_logs", "feed", "log_meta", "liq_intel")}
        return snap

    def _tool_get_bi(self, args: dict) -> dict:
        return self.bi_payload()

    def _tool_list_tools(self, args: dict) -> dict:
        return {"tools": [t.to_schema() for t in self._tools.values()]}

    def _tool_list_skills(self, args: dict) -> dict:
        return {"skills": [{"name": s.name, "description": s.desc,
                            "steps": [st.get("tool", "") for st in s.steps]}
                           for s in self._skills.values()]}

    def _tool_read_config(self, args: dict) -> dict:
        env = self._parse_env()
        keys = args.get("keys")
        if keys:
            env = {k: v for k, v in env.items() if k in keys}
        # Redact secrets
        redacted = {}
        for k, v in env.items():
            if any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASSWORD")) and len(str(v)) > 6:
                redacted[k] = str(v)[:2] + "***"
            else:
                redacted[k] = v
        return redacted

    def _tool_apply_change(self, args: dict) -> dict:
        target = args.get("target", "")
        value = args.get("value", "")
        reason = args.get("reason", "")
        if not target:
            return {"error": "target required"}

        env = self._parse_env()
        prev = env.get(target)
        needs_approval = target.upper() in FUND_KNOBS

        entry = ChangeEntry(
            id=hashlib.sha256(f"{target}:{value}:{time.time()}".encode()).hexdigest()[:12],
            ts=time.time(),
            target=target,
            prev=prev,
            new=value,
            change_type="env",
            reason=reason,
            needs_approval=needs_approval,
            approved=False,
            reverted=False,
            applied=False,
        )

        if needs_approval:
            self._append_journal(entry)
            return {"needs_approval": True, "change_id": entry.id,
                    "target": target, "prev": prev, "reason": reason,
                    "message": f"Fund-knob '{target}' requires human approval."}

        # Apply immediately
        self._write_env(target, value)
        entry.applied = True
        self._append_journal(entry)
        return {"applied": True, "change_id": entry.id,
                "target": target, "prev": prev, "new": value}

    def _tool_revert_change(self, args: dict) -> dict:
        cid = args.get("change_id", "")
        with self._lock:
            for e in reversed(self._journal):
                if e.id == cid and not e.reverted and e.applied:
                    if e.change_type == "env":
                        self._write_env(e.target, str(e.prev) if e.prev is not None else "")
                    e.reverted = True
                    self._append_journal(e)
                    return {"reverted": True, "change_id": cid, "target": e.target}
            return {"error": f"change {cid} not found or not revertible"}

    def _tool_list_changes(self, args: dict) -> dict:
        limit = args.get("limit", 10)
        with self._lock:
            recent = self._journal[-limit:]
        return {"changes": [e.to_dict() for e in recent]}

    def _tool_create_tool(self, args: dict) -> dict:
        name = args.get("name", "")
        desc = args.get("description", "")
        cmd = args.get("command", "")
        param_schema = args.get("param_schema")
        if not name or not cmd:
            return {"error": "name and command required"}
        if not self._cmd_allowed(cmd):
            return {"error": f"command not in allowlist: {cmd}"}

        tool_data = {
            "name": name,
            "description": desc,
            "command": cmd,
            "param_schema": param_schema or {"type": "object", "properties": {}},
            "created_by": "manager",
            "ts": time.time(),
        }
        path = os.path.join(_AI_TOOLS_DIR, f"{re.sub(r'[^a-z0-9_]', '_', name.lower())}.json")
        os.makedirs(_AI_TOOLS_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tool_data, f, indent=2)

        td = ToolDef(name=name, desc=desc, params=tool_data["param_schema"],
                     handler=self._make_command_handler(cmd),
                     created_by="manager", path=path)
        self._register_tool(td)
        return {"created": True, "name": name, "path": path}

    def _tool_create_skill(self, args: dict) -> dict:
        name = args.get("name", "")
        desc = args.get("description", "")
        steps = args.get("steps", [])
        if not name or not steps:
            return {"error": "name and steps required"}

        skill_data = {
            "name": name,
            "description": desc,
            "steps": steps,
            "ts": time.time(),
        }
        path = os.path.join(_AI_SKILLS_DIR, f"{re.sub(r'[^a-z0-9_]', '_', name.lower())}.json")
        os.makedirs(_AI_SKILLS_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(skill_data, f, indent=2)

        sd = SkillDef(name=name, desc=desc, steps=steps, path=path)
        self._register_skill(sd)
        return {"created": True, "name": name, "path": path}

    def _tool_run_command(self, args: dict) -> dict:
        cmd = args.get("command", "")
        if not self._cmd_allowed(cmd):
            return {"error": f"command not allowlisted: {cmd}"}
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30,
                cwd=_HERE,
            )
            return {"stdout": result.stdout[-4000:], "stderr": result.stderr[-2000:],
                    "returncode": result.returncode}
        except Exception as e:
            return {"error": str(e)}

    def _tool_spawn_agent(self, args: dict) -> dict:
        goal = args.get("goal", "")
        allowed = args.get("allowed_tools", ["get_snapshot", "get_bi", "list_tools", "read_config", "read_file"])
        budget = min(args.get("budget_steps", 6), 12)
        context = args.get("context", "")
        return self._spawn_agent_sync(goal, allowed, budget, context)

    def _tool_read_file(self, args: dict) -> dict:
        path = args.get("path", "")
        limit = args.get("limit", 200)
        full = os.path.join(_HERE, path)
        if not os.path.isfile(full):
            return {"error": f"file not found: {path}"}
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[:limit]
            return {"path": path, "lines": len(lines), "content": "".join(lines)[:6000]}
        except Exception as e:
            return {"error": str(e)}

    def _tool_add_memory(self, args: dict) -> dict:
        fact = args.get("fact", "")
        if not fact:
            return {"error": "fact required"}
        mem = self._state.setdefault("memory", [])
        mem.append(fact)
        while len(mem) > _MAX_MEMORY_FACTS:
            mem.pop(0)
        self._save_state()
        return {"added": True, "total": len(mem)}

    # ------------------------------------------------------------------
    # Agent spawning (synchronous, bounded)
    # ------------------------------------------------------------------

    def _spawn_agent_sync(self, goal: str, allowed_tools: List[str],
                          budget_steps: int, context: str = "") -> dict:
        agent_memory: List[str] = []
        messages: List[dict] = [
            {"role": "system", "content": (
                f"You are a TONI sub-agent. Goal: {goal}\n"
                f"Allowed tools: {', '.join(allowed_tools)}\n"
                f"Rules: Output exactly one JSON object per response: "
                f'{{"action":"tool_name","args":{{...}}}} or {{"reply":"final result"}}\n'
                f"Max {budget_steps} tool rounds. Be concise."
                + (f"\nContext: {context}" if context else "")
            )},
        ]
        transcript: List[dict] = []

        for _i in range(budget_steps):
            raw = self._call_llm(messages)
            parsed = self._parse_response(raw)
            transcript.append({"round": _i + 1, "raw": raw[:300]})

            action = parsed.get("action")
            if action and action in self._tools and action in allowed_tools:
                args = parsed.get("args", {})
                result = self._exec_tool(action, args)
                result_str = json.dumps(result, default=str)[:1500]
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Result for {action}: {result_str}"})
                agent_memory.append(f"{action}({json.dumps(args)[:100]}) → {result_str[:100]}")
            else:
                reply = parsed.get("reply", raw)
                agent_entry = {
                    "goal": goal,
                    "summary": reply,
                    "steps": len(transcript),
                    "ts": time.time(),
                }
                agents = self._state.setdefault("agents", [])
                agents.append(agent_entry)
                while len(agents) > 20:
                    agents.pop(0)
                self._save_state()
                return {"summary": reply, "transcript": transcript, "steps": len(transcript)}

        return {"summary": "budget exhausted", "transcript": transcript,
                "steps": budget_steps}

    # ------------------------------------------------------------------
    # Public API: status, BI, chat, approve
    # ------------------------------------------------------------------

    def status_payload(self) -> dict:
        return {
            "configured": bool(_API_KEY),
            "model": _MODEL,
            "tools_count": len(self._tools),
            "skills_count": len(self._skills),
            "agents_count": len(self._state.get("agents", [])),
            "memory_facts": len(self._state.get("memory", [])),
            "pending_approvals": [e.to_dict() for e in self._journal
                                  if e.needs_approval and not e.approved and not e.reverted],
            "recent_changes": [e.to_dict() for e in self._journal[-5:]],
        }

    def bi_payload(self) -> dict:
        """Build Power BI metrics from live state + ledgers + brain states."""
        snap = {}
        try:
            snap = self._snapshot()
        except Exception:
            snap = {}

        # Race ledger summaries
        def _ledger_stats(path: str) -> dict:
            won = lost = no_contest = 0
            total_profit = 0.0
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            row = json.loads(line.strip())
                            o = row.get("outcome", "")
                            if o == "won":
                                won += 1
                                total_profit += float(row.get("profit") or 0)
                            elif o == "lost":
                                lost += 1
                            elif o == "no-contest":
                                no_contest += 1
                        except Exception:
                            continue
            except FileNotFoundError:
                pass
            total = won + lost
            return {
                "won": won, "lost": lost, "no_contest": no_contest,
                "total": total,
                "win_rate": round(won / max(1, total), 3),
                "total_profit_usd": round(total_profit, 2),
            }

        sol_ledger = _ledger_stats(os.path.join(_DATA_DIR, "sol_race_outcomes.jsonl"))
        eth_ledger = _ledger_stats(os.path.join(_DATA_DIR, "eth_race_outcomes.jsonl"))

        # Brain model health
        def _brain_health(path: str, name: str) -> dict:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                feat_dim = len((d.get("W") or [[]])[0]) if d.get("W") else 0
                return {
                    "name": name,
                    "feat_dim": feat_dim,
                    "feat_version": d.get("feat_version", 0),
                    "steps": d.get("steps", 0),
                    "loss_ema": d.get("loss_ema"),
                    "acc_ema": d.get("acc_ema"),
                    "updated": d.get("updated"),
                }
            except Exception:
                return {"name": name, "status": "unavailable"}

        eth_brain = _brain_health(os.path.join(_HERE, "profit_brain_state.json"), "ETH")
        sol_brain = _brain_health(os.path.join(_HERE, "profit_brain_state_sol.json"), "SOL")

        cm = snap.get("competitors_meta") or {}
        hist = snap.get("hist") or {}
        pf = snap.get("performance") or {}
        sol_info = (snap.get("sol") or {}).get("race") or {}
        eth_info = snap.get("eth_race") or {}
        feeds = snap.get("feeds") or {}

        def _last(seq):
            try:
                return seq[-1] if seq else None
            except Exception:
                return None

        return {
            "ts": time.time(),
            "sol_race": sol_ledger,
            "eth_race": eth_ledger,
            "models": [eth_brain, sol_brain],
            "competitors": {
                "count_1h": cm.get("count_1h", 0),
                "unique_searchers": cm.get("unique_searchers", 0),
                "missed_by_us": cm.get("missed_by_us", 0),
            },
            "health": {
                "broadcast_ready": (snap.get("broadcast") or {}).get("ready", {}).get("ready", False),
                "sim_only": snap.get("sim_only", True),
                "armed": snap.get("armed", False),
                "block": snap.get("block"),
                "gas_gwei": snap.get("gas_gwei"),
                "eth_price_usd": snap.get("eth_price_usd"),
            },
            "performance": {
                "grade": pf.get("grade"),
                "verdict": pf.get("verdict"),
                "equity_usd": pf.get("equity_usd"),
                "equity_eth": pf.get("equity_eth"),
                "equity_sol": pf.get("equity_sol"),
                "session_pnl_usd": pf.get("session_pnl_usd"),
                "realized_usd": pf.get("realized_usd"),
                "simulated_usd": pf.get("simulated_usd"),
                "day_realized_usd": pf.get("day_realized_usd"),
                "hit_rate_pct": pf.get("hit_rate_pct"),
                "submits": pf.get("submits"),
                "wins": pf.get("wins"),
                "skips": pf.get("skips"),
                "errors": pf.get("errors"),
                "missed_comp_usd": pf.get("missed_comp_usd"),
                "missed_comp_n": pf.get("missed_comp_n"),
                "best_opp_usd": pf.get("best_opp_usd"),
                "cap_ok": pf.get("capital_ok"),
                "can_trade": pf.get("can_trade"),
                "equity_hist": (pf.get("equity_hist") or [])[-120:],
            },
            "vitals": {
                "chain": snap.get("chain"),
                "block": snap.get("block"),
                "gas_gwei": snap.get("gas_gwei"),
                "eth_price_usd": snap.get("eth_price_usd"),
                "sweep_total": snap.get("sweep_total"),
                "broadcast_ready": (snap.get("broadcast") or {}).get("ready", {}).get("ready", False),
                "armed": snap.get("armed", False),
                "sim_only": snap.get("sim_only", True),
            },
            "eth_race_live": {
                "inflight": eth_info.get("inflight", 0),
                "last_winner": (eth_info.get("last") or {}).get("winner"),
                "bid_stats": eth_info.get("bid_mult") or {},
            },
            "sol_race_live": {
                "mode": bool(sol_info.get("mode")),
                "inflight": sol_info.get("inflight", 0),
                "tuition_spent_sol": sol_info.get("tuition_spent_sol"),
                "tuition_day_sol": sol_info.get("tuition_day_sol"),
                "tip_cap_sol": sol_info.get("tip_cap_sol"),
                "tip_stats": sol_info.get("tip_mult") or {},
            },
            "feeds": {
                "sol_fee_median": _last(hist.get("sol_fee_median")),
                "sol_fee_p90": _last(hist.get("sol_fee_p90")),
                "sol_tps": _last(hist.get("sol_tps")),
                "sol_comp_1h": _last(hist.get("sol_comp_1h")),
                "sol_mp_liq": _last(hist.get("sol_mp_liq")),
                "sol_mp_mev": _last(hist.get("sol_mp_mev")),
                "comp_1h": _last(hist.get("comp_1h")),
                "comp_missed": _last(hist.get("comp_missed")),
                "gas": _last(hist.get("gas")),
                "eth": _last(hist.get("eth")),
            },
            "sol_fees_live": {
                "funded": bool(feeds.get("funded")),
                **({k: feeds[k] for k in ("median", "p90", "tps") if k in feeds}),
            },
            "config_summary": {
                "SOL_RACE_MODE": os.environ.get("SOL_RACE_MODE", "0"),
                "BROADCAST": os.environ.get("BROADCAST", ""),
                "ARMED": os.environ.get("ARMED", ""),
                "MIN_PROFIT_USD": os.environ.get("MIN_PROFIT_USD", ""),
            },
            "changes_count": len(self._journal),
            "memory_facts": len(self._state.get("memory", [])),
        }

    def briefing_payload(self) -> dict:
        """Deterministic live briefing: what's happening + what to do next."""
        bi = self.bi_payload()
        pf = bi.get("performance") or {}
        sol = bi.get("sol_race") or {}
        eth = bi.get("eth_race") or {}
        health = bi.get("health") or {}
        vit = bi.get("vitals") or {}
        sl = bi.get("sol_race_live") or {}
        el = bi.get("eth_race_live") or {}
        cm = bi.get("competitors") or {}
        models = {m.get("name"): m for m in (bi.get("models") or [])}

        equity = float(pf.get("equity_usd") or 0)
        cap_ok = bool(pf.get("cap_ok"))
        can_trade = bool(pf.get("can_trade"))
        verdict = pf.get("verdict") or ""

        happening: List[str] = []
        todo: List[str] = []
        seen: set = set()

        def add_todo(stem: str) -> None:
            if stem not in seen:
                seen.add(stem)
                todo.append(stem)

        happening.append(
            f"On {vit.get('chain') or '?'} at block {vit.get('block')}"
            f" · gas {vit.get('gas_gwei')} gwei · ETH ${vit.get('eth_price_usd')}")

        if not cap_ok:
            happening.append(
                "Wallets carry $0.00 equity — the bot is in monitor-only mode "
                "and cannot send transactions yet.")
            add_todo("Fund the sponsor + bot wallets (ETH and SOL). No capital = every "
                     "profit metric stays 0; this is the #1 blocker.")
        elif not can_trade:
            happening.append(
                "Capital is present but nothing is armed — no LIQ/ARB contract is "
                "ready to broadcast.")
            add_todo("Deploy / point LIQ_CONTRACT (GenericFlashLiquidator) so the "
                     "bot can turn capital into liquidation broadcasts.")
        elif verdict:
            happening.append(f"Engine verdict: {verdict}")

        if health.get("broadcast_ready"):
            happening.append("Broadcast is READY — the bot can send live transactions.")
        else:
            mode = "sim-only" if vit.get("sim_only") else ("armed" if vit.get("armed") else "dry")
            happening.append(
                f"Broadcast is NOT ready (currently {mode}) — all findings stay simulated.")
            add_todo("Resolve broadcast blockers (RPC/wallet keys), then flip ARMED "
                     "once you are ready to send live.")

        if sol.get("total"):
            happening.append(
                f"SOL race learning is live: {sol['won']}/{sol['total']} won "
                f"({round(sol['win_rate'] * 100)}%) — ${sol['total_profit_usd']} tracked "
                f"so far.")
            if sl.get("mode"):
                add_todo("SOL race mode is ON — with capital in, the tip bandit will "
                         "tune tips to win more of the races we currently lose.")
        else:
            happening.append("No SOL race experiences collected yet.")

        if eth.get("total"):
            happening.append(
                f"ETH race engine: {eth['total']} races, {eth['won']} won "
                f"(${eth['total_profit_usd']} tracked).")
        else:
            happening.append(
                "ETH race engine has 0 races — the GenericFlashLiquidator is not "
                "deployed, so the ETH brain never sees real competition.")
            add_todo("Deploy GenericFlashLiquidator and fund the ETH bot wallet so the "
                     "ETH brain (dim-28) can start learning real races.")

        eth_m = models.get("ETH") or {}
        sol_m = models.get("SOL") or {}
        if eth_m.get("steps"):
            happening.append(f"ETH brain: dim-{eth_m.get('feat_dim')} v{eth_m.get('feat_version')} "
                             f"· {eth_m.get('steps')} steps · loss {eth_m.get('loss_ema')}.")
        if sol_m.get("steps"):
            happening.append(f"SOL brain: dim-{sol_m.get('feat_dim')} v{sol_m.get('feat_version')} "
                             f"· {sol_m.get('steps')} steps · acc {sol_m.get('acc_ema')}.")

        cc, m1h = cm.get("count_1h", 0), cm.get("missed_by_us", 0)
        if cc:
            happening.append(f"{cc} competitor(s) active in the last hour; "
                             f"{m1h} liquidation(s) went to them instead of us.")
            if m1h:
                add_todo(f"{m1h} liquidation(s) were missed to competitors in the last hour — "
                         "review the competitor feed to see who is beating us and by how much.")

        inflight = (el.get("inflight") or 0) + (sl.get("inflight") or 0)
        if inflight:
            happening.append(f"{inflight} race(s) in flight right now — watching.")

        if not todo:
            todo.append("Nothing blocking right now. Monitor the race ledgers and let the "
                        "manager keep tuning.")

        return {
            "ts": time.time(),
            "verdict": verdict,
            "grade": pf.get("grade"),
            "happening": happening,
            "todo": todo,
            "metrics": {
                "equity_usd": equity,
                "realized_usd": pf.get("realized_usd"),
                "missed_comp_usd": pf.get("missed_comp_usd"),
                "win_rate_sol": sol.get("win_rate"),
                "races": (eth.get("total") or 0) + (sol.get("total") or 0),
                "comps_1h": cc,
                "missed_1h": m1h,
            },
        }

    def chat(self, message: str, thread_id: str = "default") -> dict:
        return self._run_orchestrator(message, thread_id)

    def approve_change(self, change_id: str, approve: bool) -> dict:
        with self._lock:
            for e in self._journal:
                if e.id == change_id and e.needs_approval and not e.approved and not e.reverted:
                    if approve:
                        if e.change_type == "env":
                            self._write_env(e.target, str(e.new))
                        e.approved = True
                        e.applied = True
                        self._append_journal(e)
                        return {"applied": True, "change_id": change_id}
                    else:
                        e.reverted = True
                        self._append_journal(e)
                        return {"rejected": True, "change_id": change_id}
            return {"error": f"change {change_id} not found or not pending"}

    # ------------------------------------------------------------------
    # .env helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_env(path: str = "") -> dict:
        path = path or _ENV_PATH
        env: Dict[str, str] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip().strip('"').strip("'")
        except FileNotFoundError:
            pass
        return env

    def _write_env(self, key: str, value: str) -> None:
        lines: List[str] = []
        found = False
        try:
            with open(_ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "=" in stripped:
                        k = stripped.split("=", 1)[0].strip()
                        if k == key:
                            lines.append(f"{key}={value}\n")
                            found = True
                            continue
                    lines.append(line)
        except FileNotFoundError:
            pass
        if not found:
            lines.append(f"{key}={value}\n")
        with open(_ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.environ[key] = value

    @staticmethod
    def _cmd_allowed(cmd: str) -> bool:
        if not cmd:
            return False
        return any(pat.match(cmd) for pat in _ALLOWED_CMD_RES)

    @staticmethod
    def _make_command_handler(cmd: str):
        def handler(args: dict) -> dict:
            final_cmd = cmd
            for k, v in (args or {}).items():
                final_cmd = final_cmd.replace(f"{{{k}}}", str(v))
            try:
                result = subprocess.run(
                    final_cmd, shell=True, capture_output=True, text=True,
                    timeout=30, cwd=_HERE,
                )
                return {"stdout": result.stdout[-4000:], "stderr": result.stderr[-2000:],
                        "returncode": result.returncode}
            except Exception as e:
                return {"error": str(e)}
        return handler
