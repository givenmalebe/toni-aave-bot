"""Tests for ai_manager — no network (LLM calls are monkeypatched)."""
import json
import os
import sys
import tempfile
import time
import pytest

# Repo root
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ai_manager as aim


# ---- Fixtures ----

@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    """Fresh AIManager with temp state/journal/tools dirs and a fake snapshot."""
    monkeypatch.setattr(aim, "_MANAGER_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(aim, "_CHANGE_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(aim, "_AI_TOOLS_DIR", str(tmp_path / "tools"))
    monkeypatch.setattr(aim, "_AI_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(aim, "_ENV_PATH", str(tmp_path / ".env"))
    # Create .env
    env_path = tmp_path / ".env"
    env_path.write_text("SOL_RACE_MODE=1\nMIN_PROFIT_USD=5\nBROADCAST=1\nSECRET_KEY=abc123xyz\nFEED_BENCH_WINDOW_S=300\n", encoding="utf-8")

    snap = {"block": 123, "gas_gwei": 25, "competitors_meta": {"count_1h": 5}}
    m = aim.AIManager(snapshot_fn=lambda: snap)
    return m


def _fake_llm(responses):
    """Return a _call_llm replacement that yields responses in order."""
    it = iter(responses)
    def _call(self_or_msg=None, *a, **kw):
        return next(it, '{"reply":"done"}')
    return _call


# ---- Tool registry ----

class TestToolRegistry:
    def test_builtins_registered(self, mgr):
        assert "get_snapshot" in mgr._tools
        assert "get_bi" in mgr._tools
        assert "apply_change" in mgr._tools
        assert "spawn_agent" in mgr._tools
        assert "read_file" in mgr._tools
        assert len(mgr._tools) >= 13

    def test_list_tools(self, mgr):
        result = mgr._tool_list_tools({})
        names = [t["name"] for t in result["tools"]]
        assert "get_snapshot" in names
        assert "create_tool" in names

    def test_list_skills_empty(self, mgr):
        result = mgr._tool_list_skills({})
        assert result["skills"] == []

    def test_get_snapshot(self, mgr):
        result = mgr._tool_get_snapshot({})
        assert result["block"] == 123

    def test_get_snapshot_compact(self, mgr):
        result = mgr._tool_get_snapshot({"compact": True})
        assert result.get("block") == 123


# ---- parse_response ----

class TestParseResponse:
    def test_valid_json(self):
        assert aim.AIManager._parse_response('{"reply":"hi"}') == {"reply": "hi"}

    def test_json_with_action(self):
        r = aim.AIManager._parse_response('{"action":"get_snapshot","args":{}}')
        assert r["action"] == "get_snapshot"

    def test_embedded_json(self):
        r = aim.AIManager._parse_response('I will check:\n{"action":"get_snapshot","args":{}}')
        assert r["action"] == "get_snapshot"

    def test_plain_text_fallback(self):
        r = aim.AIManager._parse_response("hello world")
        assert r["reply"] == "hello world"


# ---- Config read/write ----

class TestConfig:
    def test_read_config(self, mgr):
        cfg = mgr._tool_read_config({})
        assert "SOL_RACE_MODE" in cfg
        assert cfg["SOL_RACE_MODE"] == "1"

    def test_read_config_redacts_secrets(self, mgr):
        cfg = mgr._tool_read_config({})
        assert "abc123xyz" not in str(cfg.get("SECRET_KEY", ""))
        assert "***" in str(cfg.get("SECRET_KEY", ""))

    def test_read_config_filter(self, mgr):
        cfg = mgr._tool_read_config({"keys": ["FEED_BENCH_WINDOW_S"]})
        assert "FEED_BENCH_WINDOW_S" in cfg
        assert "SOL_RACE_MODE" not in cfg

    def test_apply_change_normal(self, mgr):
        result = mgr._tool_apply_change({"target": "FEED_BENCH_WINDOW_S", "value": "600"})
        assert result.get("applied") is True
        assert result.get("prev") == "300"
        cfg = mgr._tool_read_config({"keys": ["FEED_BENCH_WINDOW_S"]})
        assert cfg["FEED_BENCH_WINDOW_S"] == "600"

    def test_apply_change_fund_knob_needs_approval(self, mgr):
        result = mgr._tool_apply_change({
            "target": "SOL_RACE_MODE", "value": "0", "reason": "pause race"
        })
        assert result.get("needs_approval") is True
        assert result.get("change_id")
        # Not applied yet
        cfg = mgr._tool_read_config({"keys": ["SOL_RACE_MODE"]})
        assert cfg["SOL_RACE_MODE"] == "1"

    def test_approve_change(self, mgr):
        result = mgr._tool_apply_change({
            "target": "SOL_RACE_MODE", "value": "0", "reason": "pause"
        })
        cid = result["change_id"]
        approved = mgr.approve_change(cid, True)
        assert approved.get("applied") is True
        cfg = mgr._tool_read_config({"keys": ["SOL_RACE_MODE"]})
        assert cfg["SOL_RACE_MODE"] == "0"

    def test_reject_change(self, mgr):
        result = mgr._tool_apply_change({
            "target": "SOL_RACE_MODE", "value": "0"
        })
        cid = result["change_id"]
        rejected = mgr.approve_change(cid, False)
        assert rejected.get("rejected") is True
        cfg = mgr._tool_read_config({"keys": ["SOL_RACE_MODE"]})
        assert cfg["SOL_RACE_MODE"] == "1"


# ---- Change journal & revert ----

class TestJournal:
    def test_journal_persists(self, mgr, tmp_path):
        mgr._tool_apply_change({"target": "FEED_BENCH_WINDOW_S", "value": "900"})
        # Reconstruct from disk
        m2 = aim.AIManager(snapshot_fn=lambda: {})
        assert len(m2._journal) == 1

    def test_revert_restores_prev(self, mgr):
        mgr._tool_apply_change({"target": "FEED_BENCH_WINDOW_S", "value": "1200"})
        assert len(mgr._journal) == 1
        cid = mgr._journal[0].id
        reverted = mgr._tool_revert_change({"change_id": cid})
        assert reverted.get("reverted") is True
        cfg = mgr._tool_read_config({"keys": ["FEED_BENCH_WINDOW_S"]})
        assert cfg["FEED_BENCH_WINDOW_S"] == "300"

    def test_revert_nonexistent(self, mgr):
        result = mgr._tool_revert_change({"change_id": "doesntexist"})
        assert "error" in result

    def test_list_changes(self, mgr):
        mgr._tool_apply_change({"target": "FEED_BENCH_WINDOW_S", "value": "1500"})
        changes = mgr._tool_list_changes({"limit": 5})
        assert len(changes["changes"]) >= 1


# ---- Tool / Skill creation ----

class TestCreateToolSkill:
    def test_create_tool(self, mgr):
        result = mgr._tool_create_tool({
            "name": "run_pytest",
            "description": "Run tests",
            "command": "python -m pytest tests/test_ai_manager.py -q",
        })
        assert result.get("created") is True
        assert "run_pytest" in mgr._tools

    def test_create_tool_disallowed_command(self, mgr):
        result = mgr._tool_create_tool({
            "name": "evil",
            "description": "do evil",
            "command": "rm -rf /",
        })
        assert "error" in result

    def test_create_skill(self, mgr):
        result = mgr._tool_create_skill({
            "name": "check_health",
            "description": "Check system health",
            "steps": [
                {"tool": "get_snapshot", "args": {"compact": True}},
                {"tool": "get_bi", "args": {}},
            ],
        })
        assert result.get("created") is True
        assert "check_health" in mgr._skills

    def test_custom_tools_persist(self, mgr):
        mgr._tool_create_tool({
            "name": "test_tool", "description": "t", "command": "ls",
        })
        m2 = aim.AIManager(snapshot_fn=lambda: {})
        assert "test_tool" in m2._tools


# ---- Cmd allowed ----

class TestCmdAllowed:
    def test_pytest_allowed(self):
        assert aim.AIManager._cmd_allowed("python -m pytest tests/ -q")
    def test_rg_allowed(self):
        assert aim.AIManager._cmd_allowed("rg -n pattern")
    def test_rm_not_allowed(self):
        assert not aim.AIManager._cmd_allowed("rm -rf /")
    def test_empty_not_allowed(self):
        assert not aim.AIManager._cmd_allowed("")


# ---- run_command ----

class TestRunCommand:
    def test_run_ls(self, mgr):
        result = mgr._tool_run_command({"command": "dir"})
        assert result.get("returncode") == 0

    def test_disallowed_rejected(self, mgr):
        result = mgr._tool_run_command({"command": "echo pwned"})
        assert "error" in result


# ---- read_file ----

class TestReadFile:
    def test_read_existing(self, mgr):
        result = mgr._tool_read_file({"path": "ai_manager.py", "limit": 5})
        assert "lines" in result
        assert result["lines"] == 5

    def test_read_missing(self, mgr):
        result = mgr._tool_read_file({"path": "nonexistent.py"})
        assert "error" in result


# ---- Memory ----

class TestMemory:
    def test_add_memory(self, mgr):
        result = mgr._tool_add_memory({"fact": "ETH brain uses 28 features"})
        assert result.get("added") is True
        assert result["total"] == 1
        assert "ETH brain uses 28 features" in mgr._state["memory"]

    def test_memory_capped(self, mgr):
        for i in range(35):
            mgr._tool_add_memory({"fact": f"fact {i}"})
        assert len(mgr._state["memory"]) <= 30


# ---- BI payload ----

class TestBIPayload:
    def test_bi_shape(self, mgr):
        bi = mgr.bi_payload()
        assert "ts" in bi
        assert "sol_race" in bi
        assert "eth_race" in bi
        assert "models" in bi
        assert "competitors" in bi
        assert "health" in bi

    def test_bi_with_ledgers(self, mgr, tmp_path, monkeypatch):
        ledger_path = str(tmp_path / "eth_race_outcomes.jsonl")
        monkeypatch.setattr(aim, "_DATA_DIR", str(tmp_path))
        with open(ledger_path, "w") as f:
            f.write(json.dumps({"outcome": "won", "profit": 12.5}) + "\n")
            f.write(json.dumps({"outcome": "lost", "profit": 0}) + "\n")
            f.write(json.dumps({"outcome": "won", "profit": 8.0}) + "\n")
        bi = mgr.bi_payload()
        assert bi["eth_race"]["won"] == 2
        assert bi["eth_race"]["lost"] == 1
        assert abs(bi["eth_race"]["win_rate"] - 2 / 3) < 0.001

    def test_bi_models_from_state_files(self, mgr, tmp_path, monkeypatch):
        state_eth = tmp_path / "profit_brain_state.json"
        state_eth.write_text(json.dumps({
            "W": [[1.0] * 28, [2.0] * 28],
            "steps": 150,
            "loss_ema": 0.32,
            "acc_ema": 0.81,
            "feat_version": 1,
        }))
        monkeypatch.setattr(aim, "_HERE", str(tmp_path))
        bi = mgr.bi_payload()
        eth_model = [m for m in bi["models"] if m.get("name") == "ETH"]
        assert eth_model
        assert eth_model[0]["steps"] == 150


# ---- Briefing payload ----

class TestBriefing:
    def test_briefing_shape(self, mgr):
        b = mgr.briefing_payload()
        assert "happening" in b
        assert "todo" in b
        assert "metrics" in b
        assert b["metrics"]["equity_usd"] == 0.0

    def test_briefing_todo_covers_funding_and_deploy(self, mgr):
        b = mgr.briefing_payload()
        joined = " ".join(b["todo"]).lower()
        assert "fund" in joined
        assert "deploy" in joined
        assert b["happening"]


# ---- Chat orchestration (fake LLM) ----

class TestChat:
    def test_chat_reply(self, mgr, monkeypatch):
        monkeypatch.setattr(aim.AIManager, "_call_llm", lambda self, msgs, **kw: '{"reply":"all good"}')
        result = mgr.chat("how are we doing?")
        assert result["reply"] == "all good"
        assert result["thread_id"] == "default"
        assert len(result["transcript"]) == 1

    def test_chat_tool_call(self, mgr, monkeypatch):
        """LLM calls get_snapshot once, then replies."""
        responses = iter([
            '{"action":"get_snapshot","args":{"compact":true}}',
            '{"reply":"snapshot shows block 123"}',
        ])
        monkeypatch.setattr(aim.AIManager, "_call_llm",
                            lambda self, msgs, **kw: next(responses))
        result = mgr.chat("what's the snapshot?")
        assert result["reply"] == "snapshot shows block 123"
        assert len(result["transcript"]) == 3  # tool call + tool result + final

    def test_chat_persists_thread(self, mgr, monkeypatch):
        monkeypatch.setattr(aim.AIManager, "_call_llm", lambda self, msgs, **kw: '{"reply":"ok"}')
        mgr.chat("hello", thread_id="t1")
        mgr.chat("bye", thread_id="t1")
        thread = mgr._state["threads"]["t1"]
        assert len(thread) == 4  # user+assistant + user+assistant


# ---- Agent spawning ----

class TestAgentSpawn:
    def test_spawn_agent(self, mgr, monkeypatch):
        monkeypatch.setattr(aim.AIManager, "_call_llm",
                            lambda self, msgs, **kw: '{"reply":"agent done"}')
        result = mgr._spawn_agent_sync(
            goal="analyze competitors",
            allowed_tools=["get_snapshot"],
            budget_steps=3,
        )
        assert "summary" in result
        assert result["steps"] >= 1

    def test_spawn_agent_budget_cap(self, mgr, monkeypatch):
        call_count = [0]
        def fake_llm(self, msgs, **kw):
            call_count[0] += 1
            return '{"action":"get_snapshot","args":{}}'
        monkeypatch.setattr(aim.AIManager, "_call_llm", fake_llm)
        result = mgr._spawn_agent_sync(
            goal="loop forever",
            allowed_tools=["get_snapshot"],
            budget_steps=4,
        )
        assert result["steps"] == 4
        assert call_count[0] == 4

    def test_spawn_agent_persists(self, mgr, monkeypatch):
        monkeypatch.setattr(aim.AIManager, "_call_llm",
                            lambda self, msgs, **kw: '{"reply":"done"}')
        mgr._spawn_agent_sync("test goal", ["get_snapshot"], 2)
        assert len(mgr._state["agents"]) == 1
        assert mgr._state["agents"][0]["goal"] == "test goal"


# ---- status_payload ----

class TestStatusPayload:
    def test_status_shape(self, mgr):
        s = mgr.status_payload()
        assert "configured" in s
        assert "tools_count" in s
        assert "skills_count" in s
        assert "memory_facts" in s
        assert "pending_approvals" in s
        assert "recent_changes" in s

    def test_status_configured(self, mgr, monkeypatch):
        monkeypatch.setattr(aim, "_API_KEY", "sk-test")
        assert mgr.status_payload()["configured"] is True

    def test_status_not_configured(self, mgr, monkeypatch):
        monkeypatch.setattr(aim, "_API_KEY", "")
        assert mgr.status_payload()["configured"] is False
