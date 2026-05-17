#!/usr/bin/env python3
"""
usage2 meter — local + crowd-informed token / cost / quota meter
for Claude Code subscription users (Pro / Max 5x / Max 20x).

Every quota refresh writes a timestamped report to reports/. Calibration
reads from that directory (your own samples), and optionally merges
crowd_reports/ contributed via the public repo. Each report records
tier, panel %s, per-model trailing-window token totals, and pricing
snapshot date — so the picture stays accurate as Anthropic adjusts limits.

Modes:
  summary           tokens + $ + % session/week + signals + calibration (default)
  quick             one-line totals
  agents            per-subagent attribution
  mark <n> [--quota]   checkpoint at byte offset (optionally w/ quota snapshot)
  since <n>         token + $ + quota delta since checkpoint
  marks             list saved checkpoints
  drop <n>          delete a checkpoint
  raw               JSON dump (for downstream tools)
  quota             force-refresh quota panel (writes a report)
  sample            alias for quota (kept for memory-file compatibility)
  calibrate         show derived tokens-per-percent estimates from reports/
  reports           list saved reports (own + crowd)
  contribute        print anonymized JSON for sharing to public repo
  tier [<t>]        show/set subscription tier (pro / max5x / max20x)

Flags: --no-quota (skip quota refresh on summary), --crowd (merge community data)
"""

import json
import os
import re
import sys
import time
import uuid
import subprocess
from pathlib import Path
from datetime import datetime, timezone

SCHEMA_VERSION = 1
SKILL_DIR = Path(__file__).parent
PROJECTS_DIR = Path.home() / ".claude" / "projects"
MARKS_DIR = SKILL_DIR / "marks"
REPORTS_DIR = SKILL_DIR / "reports"
CROWD_DIR = SKILL_DIR / "crowd_reports"
CONFIG_FILE = SKILL_DIR / "config.json"
QUOTA_CACHE = SKILL_DIR / "quota_cache.json"
CAPTURE_SH = SKILL_DIR / "capture.sh"

QUOTA_CACHE_TTL = 600  # 10 min
CONTAMINATION_THRESHOLD_USD = 5.0  # concurrent activity above this flags a report as contaminated

# Anthropic API rates ($ per million tokens). Source:
# https://platform.claude.com/docs/en/docs/about-claude/pricing (verified 2026-05-17).
# Opus 4.5+ pricing is 1/3 of Opus 4.1 — do NOT assume Opus = $15/$75.
PRICING_DATE = "2026-05-17"
RATES = {
    "claude-opus-4-7":     {"input":  5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m":  6.25, "cache_write_1h": 10.00},
    "claude-opus-4-6":     {"input":  5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m":  6.25, "cache_write_1h": 10.00},
    "claude-opus-4-5":     {"input":  5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m":  6.25, "cache_write_1h": 10.00},
    "claude-opus-4-1":     {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write_5m": 18.75, "cache_write_1h": 30.00},
    "claude-sonnet-4-6":   {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m":  3.75, "cache_write_1h":  6.00},
    "claude-sonnet-4-5":   {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m":  3.75, "cache_write_1h":  6.00},
    "claude-haiku-4-5":    {"input":  1.00, "output":  5.00, "cache_read": 0.10, "cache_write_5m":  1.25, "cache_write_1h":  2.00},
    "claude-haiku-3-5":    {"input":  0.80, "output":  4.00, "cache_read": 0.08, "cache_write_5m":  1.00, "cache_write_1h":  1.60},
}
DEFAULT_RATE_KEY = "claude-sonnet-4-6"

AGENT_TYPE_MODEL = {
    "Explore":         "claude-haiku-4-5",
    "general-purpose": "claude-sonnet-4-6",
    "Plan":            "claude-sonnet-4-6",
    "code-reviewer":   "claude-sonnet-4-6",
}

TIERS = {
    "pro":     {"name": "Pro",    "monthly_fee_usd":  20},
    "max5x":   {"name": "Max 5x", "monthly_fee_usd": 100},
    "max20x":  {"name": "Max 20x","monthly_fee_usd": 200},
}

# Skip "model" values that aren't real billable models
SKIP_MODELS = {"<synthetic>", "synthetic", None, "", "unknown"}


# ─────────────────────────── transcript parsing ───────────────────────────

def project_slug(path: Path) -> str:
    return re.sub(r"[/_]", "-", str(path.resolve()).rstrip("/"))


def current_session_jsonl(cwd: Path | None = None) -> Path | None:
    """Find the JSONL for the calling Claude Code session.

    Preference order:
      1. $CLAUDE_CODE_SESSION_ID — set by Claude Code in its own env; authoritative.
         Critical when multiple CC instances run in the same project (each writes
         its own JSONL; "most recently modified" would mis-attribute work).
      2. Most-recently-modified *.jsonl in the project dir (fallback for older CC
         or non-CC invocations).
    """
    cwd = cwd or Path.cwd()
    p = PROJECTS_DIR / project_slug(cwd)
    if not p.exists(): return None
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        candidate = p / f"{sid}.jsonl"
        if candidate.exists():
            return candidate
    files = list(p.glob("*.jsonl"))
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def parse_records(path: Path, byte_offset: int = 0):
    with open(path, "rb") as f:
        if byte_offset:
            f.seek(byte_offset); f.readline()
        for raw in f:
            try: yield json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError): continue


def ts_seconds(ts_str):
    if not ts_str: return 0
    try: return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except: return 0


def collect(records):
    main = {"turns": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "ephemeral_1h": 0, "ephemeral_5m": 0, "per_turn": [], "by_model": {}}
    subagents = []
    tool_call_count = 0
    for r in records:
        rtype = r.get("type")
        ts = r.get("timestamp")
        if rtype == "assistant" and not r.get("isSidechain"):
            msg = r.get("message") or {}
            usage = msg.get("usage")
            model = msg.get("model")
            if usage and model not in SKIP_MODELS:
                main["turns"] += 1
                in_t = usage.get("input_tokens", 0) or 0
                out_t = usage.get("output_tokens", 0) or 0
                cr_t = usage.get("cache_read_input_tokens", 0) or 0
                cw_t = usage.get("cache_creation_input_tokens", 0) or 0
                cc = usage.get("cache_creation") or {}
                e1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
                e5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
                main["input_tokens"] += in_t
                main["output_tokens"] += out_t
                main["cache_read_input_tokens"] += cr_t
                main["cache_creation_input_tokens"] += cw_t
                main["ephemeral_1h"] += e1h
                main["ephemeral_5m"] += e5m
                main["per_turn"].append({"ts": ts, "in": in_t, "out": out_t,
                                          "cache_r": cr_t, "cache_w": cw_t, "model": model})
                bm = main["by_model"].setdefault(model,
                    {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0, "turns": 0})
                bm["input"] += in_t
                bm["output"] += out_t
                bm["cache_read"] += cr_t
                bm["cache_write_5m"] += e5m
                bm["cache_write_1h"] += e1h
                bm["turns"] += 1
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_call_count += 1
        if rtype == "user":
            tur = r.get("toolUseResult")
            if isinstance(tur, dict) and tur.get("agentType"):
                u = tur.get("usage") or {}
                cc = u.get("cache_creation") or {}
                subagents.append({
                    "ts": ts,
                    "agentType": tur.get("agentType") or "unknown",
                    "agentName": tur.get("agentName"),
                    "totalTokens": tur.get("totalTokens", 0) or 0,
                    "input_tokens": u.get("input_tokens", 0) or 0,
                    "output_tokens": u.get("output_tokens", 0) or 0,
                    "cache_read_input_tokens": u.get("cache_read_input_tokens", 0) or 0,
                    "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0) or 0,
                    "ephemeral_1h": cc.get("ephemeral_1h_input_tokens", 0) or 0,
                    "ephemeral_5m": cc.get("ephemeral_5m_input_tokens", 0) or 0,
                    "durationMs": tur.get("totalDurationMs", 0) or 0,
                    "toolUseCount": tur.get("totalToolUseCount", 0) or 0,
                    "prompt_preview": ((tur.get("prompt") or "")[:120]).replace("\n", " "),
                    "status": tur.get("status"),
                    "assumed_model": AGENT_TYPE_MODEL.get(tur.get("agentType"), DEFAULT_RATE_KEY),
                })
    return main, subagents, tool_call_count


# ─────────────────────────── cost / weighting ───────────────────────────

def rates_for(model: str) -> dict:
    return RATES.get(model, RATES[DEFAULT_RATE_KEY])


def cost_of(b: dict, model: str) -> float:
    r = rates_for(model)
    return (b.get("input", 0) * r["input"]
          + b.get("output", 0) * r["output"]
          + b.get("cache_read", 0) * r["cache_read"]
          + b.get("cache_write_5m", 0) * r["cache_write_5m"]
          + b.get("cache_write_1h", 0) * r["cache_write_1h"]) / 1_000_000


def main_cost(m: dict) -> float:
    return sum(cost_of(bm, model) for model, bm in m["by_model"].items())


def subagent_cost(s: dict) -> float:
    return cost_of({"input": s["input_tokens"], "output": s["output_tokens"],
                    "cache_read": s["cache_read_input_tokens"],
                    "cache_write_5m": s["ephemeral_5m"],
                    "cache_write_1h": s["ephemeral_1h"]}, s["assumed_model"])


def weighted_input_equiv(b: dict, model: str) -> float:
    r = rates_for(model)
    return (b.get("input", 0)
          + b.get("output", 0) * (r["output"] / r["input"])
          + b.get("cache_read", 0) * (r["cache_read"] / r["input"])
          + b.get("cache_write_5m", 0) * (r["cache_write_5m"] / r["input"])
          + b.get("cache_write_1h", 0) * (r["cache_write_1h"] / r["input"]))


# ─────────────────────────── window aggregation ───────────────────────────

def find_active_jsonls(window_seconds: float, exclude: Path | None = None) -> list[Path]:
    """Return all *.jsonl across ~/.claude/projects/*/ modified within the window.

    Used for account-scope token aggregation when the user runs multiple
    concurrent Claude Code sessions. Each session has its own JSONL, but the
    /usage panel reflects the whole account — so the calibration needs to see
    ALL of them to correctly attribute tokens-per-percent.
    """
    cutoff = time.time() - window_seconds
    out = []
    if not PROJECTS_DIR.exists(): return out
    for proj in PROJECTS_DIR.iterdir():
        if not proj.is_dir(): continue
        for f in proj.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff: continue
                if exclude and f.resolve() == exclude.resolve(): continue
                out.append(f)
            except: continue
    return out


def tokens_in_window(jsonl: Path | list[Path], window_seconds: float) -> dict:
    """Sum per-model trailing-window token counts.

    Pass a single Path for session-scope, or a list for account-scope.
    """
    sources = jsonl if isinstance(jsonl, list) else [jsonl]
    cutoff = time.time() - window_seconds
    by_model = {}
    def _add(model, u, cc):
        bm = by_model.setdefault(model,
            {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0})
        bm["input"] += u.get("input_tokens", 0) or 0
        bm["output"] += u.get("output_tokens", 0) or 0
        bm["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
        bm["cache_write_5m"] += cc.get("ephemeral_5m_input_tokens", 0) or 0
        bm["cache_write_1h"] += cc.get("ephemeral_1h_input_tokens", 0) or 0
    for src in sources:
        for r in parse_records(src):
            ts = ts_seconds(r.get("timestamp"))
            if ts < cutoff: continue
            rtype = r.get("type")
            if rtype == "assistant" and not r.get("isSidechain"):
                msg = r.get("message") or {}
                u = msg.get("usage")
                model = msg.get("model")
                if u and model not in SKIP_MODELS:
                    _add(model, u, u.get("cache_creation") or {})
            if rtype == "user":
                tur = r.get("toolUseResult")
                if isinstance(tur, dict) and tur.get("agentType"):
                    u = tur.get("usage") or {}
                    model = AGENT_TYPE_MODEL.get(tur.get("agentType"), DEFAULT_RATE_KEY)
                    _add(model, u, u.get("cache_creation") or {})
    raw_total = sum(sum(bm.values()) for bm in by_model.values())
    weighted = sum(weighted_input_equiv(bm, mdl) for mdl, bm in by_model.items())
    dollars = sum(cost_of(bm, mdl) for mdl, bm in by_model.items())
    return {"by_model": by_model, "raw_total": raw_total,
            "weighted_input_equiv": int(weighted), "dollars": round(dollars, 4)}


# ─────────────────────────── config / cache / reports ───────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try: return json.loads(CONFIG_FILE.read_text())
        except: pass
    return {}


def save_config(cfg: dict):
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def get_or_mint_anon_id() -> str:
    cfg = load_config()
    if not cfg.get("anonymous_id"):
        cfg["anonymous_id"] = str(uuid.uuid4())
        save_config(cfg)
    return cfg["anonymous_id"]


def load_quota_cache() -> dict | None:
    if QUOTA_CACHE.exists():
        try: return json.loads(QUOTA_CACHE.read_text())
        except: pass
    return None


def save_quota_cache(data: dict):
    QUOTA_CACHE.write_text(json.dumps(data, indent=2))


def save_report(report: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    f = REPORTS_DIR / f"{stamp}.json"
    f.write_text(json.dumps(report, indent=2))
    return f


def load_reports(include_crowd: bool = False) -> list[dict]:
    reports = []
    if REPORTS_DIR.exists():
        for p in sorted(REPORTS_DIR.glob("*.json")):
            try: reports.append(json.loads(p.read_text()))
            except: pass
    if include_crowd and CROWD_DIR.exists():
        for p in sorted(CROWD_DIR.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                d["_crowd"] = True
                reports.append(d)
            except: pass
    return reports


# ─────────────────────────── /usage panel capture ───────────────────────────

def capture_quota_panel() -> str:
    r = subprocess.run(["bash", str(CAPTURE_SH)], capture_output=True, text=True, timeout=60)
    return r.stdout


def parse_quota_panel(text: str) -> dict:
    out = {"session_pct": None, "session_reset": None,
           "week_all_pct": None, "week_reset": None,
           "week_sonnet_pct": None, "week_sonnet_reset": None}
    section = None
    for line in text.splitlines():
        s = line.strip()
        if "Current session" in s: section = "session"
        elif "Current week (all models)" in s: section = "week_all"
        elif "Current week (Sonnet only)" in s: section = "week_sonnet"
        elif s.startswith("What's contributing") or s.startswith("Extra usage"): section = None
        m = re.search(r"(\d+)%\s*used", s)
        if m and section and out.get(f"{section}_pct") is None:
            out[f"{section}_pct"] = int(m.group(1))
        m2 = re.search(r"Resets\s+(.+?)\s*$", s)
        if m2 and section:
            key = "session_reset" if section == "session" else "week_reset" if section == "week_all" else "week_sonnet_reset"
            if out.get(key) is None: out[key] = m2.group(1).strip()
    return out


def refresh_quota(force: bool = False, write_report: bool = True, jsonl: Path | None = None) -> dict | None:
    cache = load_quota_cache()
    if cache and not force:
        if time.time() - cache.get("ts", 0) < QUOTA_CACHE_TTL:
            return cache
    try:
        text = capture_quota_panel()
        parsed = parse_quota_panel(text)
        parsed["ts"] = time.time()
        parsed["panel_text"] = text
        save_quota_cache(parsed)
        if write_report and jsonl:
            cfg = load_config()
            tier_key = cfg.get("tier")
            tier_info = TIERS.get(tier_key, {})
            # Account-scope: all local JSONLs modified in the window
            others_5h = find_active_jsonls(5 * 3600, exclude=jsonl)
            others_7d = find_active_jsonls(7 * 86400, exclude=jsonl)
            session_5h = tokens_in_window(jsonl, 5 * 3600)
            session_7d = tokens_in_window(jsonl, 7 * 86400)
            account_5h = tokens_in_window([jsonl] + others_5h, 5 * 3600)
            account_7d = tokens_in_window([jsonl] + others_7d, 7 * 86400)
            # Concurrent contamination: sum of dollar consumption from OTHER sessions
            concurrent_5h_dollars = round(sum(tokens_in_window(o, 5 * 3600)["dollars"] for o in others_5h), 4)
            concurrent_7d_dollars = round(sum(tokens_in_window(o, 7 * 86400)["dollars"] for o in others_7d), 4)
            contaminated = concurrent_5h_dollars > CONTAMINATION_THRESHOLD_USD
            # Top-level dollars/weighted = account scope (for calibration use)
            save_report({
                "schema_version": SCHEMA_VERSION,
                "timestamp_iso": datetime.now(timezone.utc).isoformat(),
                "timestamp_local": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "epoch": int(time.time()),
                "tier": tier_key,
                "tier_monthly_usd": tier_info.get("monthly_fee_usd"),
                "anonymous_id": get_or_mint_anon_id(),
                "pricing_snapshot_date": PRICING_DATE,
                "panel": {k: parsed.get(k) for k in
                          ("session_pct", "session_reset", "week_all_pct", "week_reset",
                           "week_sonnet_pct", "week_sonnet_reset")},
                "trailing_5h": {**account_5h, "session_scope": session_5h},
                "trailing_7d": {**account_7d, "session_scope": session_7d},
                "concurrent_5h": [p.stem for p in others_5h],
                "concurrent_7d": [p.stem for p in others_7d],
                "concurrent_5h_dollars": concurrent_5h_dollars,
                "concurrent_7d_dollars": concurrent_7d_dollars,
                "contaminated": contaminated,
                "contamination_threshold_usd": CONTAMINATION_THRESHOLD_USD,
            })
        return parsed
    except Exception:
        return cache  # stale is better than nothing


# ─────────────────────────── calibration ───────────────────────────

def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def slope_from_reports(reports: list[dict], pct_key: str, window_key: str,
                       tier_filter: str | None = None) -> dict | None:
    """Median of consecutive-pair slopes within same (anonymous_id, tier) group.

    Uses *dollars* as the calibration unit — naturally weights Opus / Sonnet / Haiku
    tokens correctly relative to each other (and across input / output / cache types)
    because Anthropic's per-model rate ratios are baked into the dollar conversion.

    Falls back to legacy `weighted_input_equiv` for old reports without dollars.
    """
    samples = []
    contaminated_skipped = 0
    legacy_skipped = 0
    for r in reports:
        if tier_filter and r.get("tier") != tier_filter: continue
        # Skip contaminated reports (concurrent activity > threshold)
        if r.get("contaminated") is True:
            contaminated_skipped += 1
            continue
        # Skip legacy reports without contamination tracking — can't trust their dollars
        if r.get("contaminated") is None and "concurrent_5h_dollars" not in r:
            legacy_skipped += 1
            continue
        pct = (r.get("panel") or {}).get(pct_key)
        win = r.get(window_key) or {}
        value = win.get("dollars")
        anon = r.get("anonymous_id", "anon")
        epoch = r.get("epoch", 0)
        if pct is None or value is None or pct <= 0: continue
        samples.append({"anon": anon, "epoch": epoch, "pct": pct, "dollars": value})
    if len(samples) < 2:
        if contaminated_skipped or legacy_skipped:
            return {"insufficient": True, "contaminated_skipped": contaminated_skipped, "legacy_skipped": legacy_skipped}
        return None
    by_anon = {}
    for s in samples: by_anon.setdefault(s["anon"], []).append(s)
    slopes = []
    for grp in by_anon.values():
        grp.sort(key=lambda x: x["epoch"])
        for i in range(1, len(grp)):
            dp = grp[i]["pct"] - grp[i-1]["pct"]
            dd = grp[i]["dollars"] - grp[i-1]["dollars"]
            if dp > 0 and dd > 0: slopes.append(dd / dp)
    if not slopes:
        slopes = [s["dollars"] / s["pct"] for s in samples if s["pct"] > 0]
    if not slopes: return None
    m = median(slopes)
    return {"dollars_per_percent": round(m, 4),
            "est_full_capacity_dollars": round(m * 100, 2),
            "samples": len(samples), "slopes_used": len(slopes)}


def calibration_estimates(include_crowd: bool = False, tier_filter: str | None = None) -> dict:
    reports = load_reports(include_crowd=include_crowd)
    return {
        "report_count": len(reports),
        "own_count": sum(1 for r in reports if not r.get("_crowd")),
        "crowd_count": sum(1 for r in reports if r.get("_crowd")),
        "session": slope_from_reports(reports, "session_pct", "trailing_5h", tier_filter),
        "week_all": slope_from_reports(reports, "week_all_pct", "trailing_7d", tier_filter),
        "week_sonnet": slope_from_reports(reports, "week_sonnet_pct", "trailing_7d", tier_filter),
    }


# ─────────────────────────── formatting ───────────────────────────

def fmt(n) -> str:
    n = int(n)
    if n >= 1_000_000: return f"{n / 1_000_000:.2f}M"
    if n >= 1_000: return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_dollars(d: float) -> str:
    if d >= 100: return f"${d:.0f}"
    if d >= 1: return f"${d:.2f}"
    if d >= 0.01: return f"${d:.3f}"
    return f"${d:.4f}"


def main_thread_total(m): return (m["input_tokens"] + m["output_tokens"]
                                  + m["cache_read_input_tokens"] + m["cache_creation_input_tokens"])


def cache_hit_pct(m):
    seen = m["cache_read_input_tokens"] + m["cache_creation_input_tokens"] + m["input_tokens"]
    return (m["cache_read_input_tokens"] / seen * 100) if seen else 0.0


def new_tokens(m, subs):
    """Unique content tokens (excludes cache_read replay). The 'actual work' number."""
    main_new = m["input_tokens"] + m["output_tokens"] + m["cache_creation_input_tokens"]
    sub_new = sum(s["input_tokens"] + s["output_tokens"] + s["cache_creation_input_tokens"] for s in subs)
    return main_new + sub_new


# ─────────────────────────── reports ───────────────────────────

def report_summary(jsonl: Path, byte_offset: int = 0, label: str = "",
                   skip_quota: bool = False, include_crowd: bool = False):
    records = list(parse_records(jsonl, byte_offset))
    m, subs, tool_calls = collect(records)
    main_total = main_thread_total(m)
    sub_total = sum(s["totalTokens"] for s in subs)
    grand = main_total + sub_total
    m_cost = main_cost(m)
    s_cost = sum(subagent_cost(s) for s in subs)
    total_cost = m_cost + s_cost
    nt = new_tokens(m, subs)

    cfg = load_config()
    tier = cfg.get("tier")
    tier_info = TIERS.get(tier) if tier else None

    print(f"## /usage2 summary{(' — ' + label) if label else ''}")
    print(f"## Source: {jsonl.name}  ({'env-pinned' if os.environ.get('CLAUDE_CODE_SESSION_ID') else 'mtime-picked'})")
    print()
    print("### Main thread")
    if m["turns"] == 0:
        print("  (no completed turns in this range)")
    else:
        print(f"  Turns: {m['turns']}  ·  tool calls: {tool_calls}")
        print(f"  Input (fresh):   {fmt(m['input_tokens']):>8}")
        print(f"  Output:          {fmt(m['output_tokens']):>8}")
        print(f"  Cache read:      {fmt(m['cache_read_input_tokens']):>8}  ({cache_hit_pct(m):.0f}% of all input)")
        print(f"  Cache write:     {fmt(m['cache_creation_input_tokens']):>8}  (1h: {fmt(m['ephemeral_1h'])} · 5m: {fmt(m['ephemeral_5m'])})")
        if len(m["by_model"]) > 1:
            for mdl, bm in sorted(m["by_model"].items(), key=lambda kv: -kv[1]["turns"]):
                print(f"    {mdl}: {bm['turns']} turns")
        print(f"  Main-thread total:  {fmt(main_total)} tok  ·  {fmt_dollars(m_cost)} API-equiv")
    print()

    if subs:
        print(f"### Subagents ({len(subs)} spawns, {fmt(sub_total)} tok, {fmt_dollars(s_cost)})")
        by_type = {}
        for s in subs: by_type.setdefault(s["agentType"], []).append(s)
        for t in sorted(by_type, key=lambda k: -sum(s["totalTokens"] for s in by_type[k])):
            spawns = by_type[t]
            tt = sum(s["totalTokens"] for s in spawns)
            tc = sum(subagent_cost(s) for s in spawns)
            print(f"  {t} × {len(spawns)} (assumed {AGENT_TYPE_MODEL.get(t, 'sonnet')}): {fmt(tt)} · {fmt_dollars(tc)}")
            for s in spawns:
                pp = s["prompt_preview"][:70]
                print(f"    └─ {fmt(s['totalTokens']):>6}  {fmt_dollars(subagent_cost(s)):>7}  "
                      f"in {fmt(s['input_tokens'])} out {fmt(s['output_tokens'])} cache-r {fmt(s['cache_read_input_tokens'])}  "
                      f"{s['durationMs']/1000:.0f}s  {s['toolUseCount']} tools  \"{pp}…\"")
        print()

    print(f"### Totals")
    print(f"  Grand total (all categories):  {fmt(grand)} tok  ·  {fmt_dollars(total_cost)} API-equiv")
    print(f"  New content tokens:            {fmt(nt)} tok    ← actual work (excludes cache_read replay)")
    if tier_info:
        days = total_cost / (tier_info["monthly_fee_usd"] / 30) if tier_info["monthly_fee_usd"] else 0
        print(f"  Tier: {tier_info['name']} (${tier_info['monthly_fee_usd']}/mo)  →  {days:.1f} days of subscription fee in API-equiv value")
    print()

    if not skip_quota:
        # User-triggered summary always force-refreshes so each /usage2 invocation
        # writes a fresh report. Cache is for cheap-polling (quick) only.
        quota = refresh_quota(force=True, jsonl=jsonl)
        # Concurrent-session detection — filter to JSONLs with meaningful token
        # activity in the actual rolling 5h window ($1+ API-equiv). mtime alone is
        # too noisy (hooks bump mtime on otherwise-idle files).
        others = find_active_jsonls(5 * 3600, exclude=jsonl)
        scored = []
        for o in others:
            try:
                w = tokens_in_window(o, 5 * 3600)
                if w["dollars"] >= 1.0:
                    scored.append((o, w["dollars"]))
            except: pass
        scored.sort(key=lambda x: -x[1])
        if scored:
            total_other_dollars = sum(d for _, d in scored)
            print(f"### ⚠ Concurrent sessions consuming the same quota (last 5h): {len(scored)}  ·  total {fmt_dollars(total_other_dollars)}")
            for o, d in scored[:5]:
                slug = o.parent.name.lstrip("-").replace("-", "/")
                print(f"  • {o.stem[:13]}…  {fmt_dollars(d):>7}  in /{slug[:80]}")
            if len(scored) > 5: print(f"  …+{len(scored)-5} more (run `usage2 raw` for full list)")
            print(f"  ▸ Panel %s reflect ALL of these. To avoid distorting your own calibration,")
            print(f"    pause concurrent agents during a sampling run, or accept the noise.")
            print()
        print("### Rolling quota windows")
        if not quota:
            print("  (quota panel unavailable)")
        else:
            age = time.time() - quota.get("ts", 0)
            age_str = f"{int(age)}s ago" if age < 90 else f"{int(age/60)}min ago"
            if quota.get("session_pct") is not None:
                print(f"  Session (5h):    {quota['session_pct']:3d}%   resets {quota.get('session_reset', '?')}")
            if quota.get("week_all_pct") is not None:
                print(f"  Week (all):      {quota['week_all_pct']:3d}%   resets {quota.get('week_reset', '?')}")
            if quota.get("week_sonnet_pct") is not None:
                print(f"  Week (Sonnet):   {quota['week_sonnet_pct']:3d}%   resets {quota.get('week_sonnet_reset', '?')}")
            print(f"  ({age_str})")
        print()

    est = calibration_estimates(include_crowd=include_crowd, tier_filter=tier)
    all_reports = load_reports(include_crowd=include_crowd)
    clean = sum(1 for r in all_reports if r.get("contaminated") is False)
    contam = sum(1 for r in all_reports if r.get("contaminated") is True)
    legacy = sum(1 for r in all_reports if r.get("contaminated") is None and "concurrent_5h_dollars" not in r)
    crowd_note = f" + {est['crowd_count']} crowd" if est['crowd_count'] else ""
    parts = [f"{clean} clean"]
    if contam: parts.append(f"{contam} contaminated")
    if legacy: parts.append(f"{legacy} legacy/no-contamination-data")
    print(f"### Calibration ({', '.join(parts)}{crowd_note}{', tier=' + tier if tier else ''})")

    def per_model_input_for(dollars_per_pct: float) -> str:
        """Show how many input tokens of each model 1% of quota allows."""
        parts = []
        for model_short, key in (("Opus", "claude-opus-4-7"), ("Sonnet", "claude-sonnet-4-6"), ("Haiku", "claude-haiku-4-5")):
            rate = RATES[key]["input"]  # $/M tokens
            tokens_per_pct = dollars_per_pct / rate * 1_000_000
            parts.append(f"{model_short} {fmt(tokens_per_pct)}")
        return " · ".join(parts)

    def print_window(name: str, e: dict | None):
        if e is None:
            print(f"  {name:<24} (need ≥2 clean reports with delta)")
            return
        if e.get("insufficient"):
            skipped = f"{e.get('contaminated_skipped', 0)} contaminated"
            if e.get("legacy_skipped"): skipped += f", {e['legacy_skipped']} legacy"
            print(f"  {name:<24} (need ≥2 clean reports — {skipped} skipped)")
            return
        d_pct = e["dollars_per_percent"]
        cap = e["est_full_capacity_dollars"]
        print(f"  {name:<24} 1% ≈ {fmt_dollars(d_pct)}  ·  100% ≈ {fmt_dollars(cap)}  ·  {e['slopes_used']} slopes from {e['samples']} clean samples")
        print(f"  {' ':<24}   per 1% in pure-input tokens: {per_model_input_for(d_pct)}")

    print_window("Session 5h:",         est["session"])
    print_window("Week 7d (all):",      est["week_all"])
    print_window("Week 7d (Sonnet):",   est["week_sonnet"])
    if not include_crowd and CROWD_DIR.exists() and any(CROWD_DIR.glob("*.json")):
        print(f"  (Hint: pass --crowd to include {sum(1 for _ in CROWD_DIR.glob('*.json'))} community-contributed reports)")
    print()

    if m["turns"] >= 3:
        print("### Efficiency signals")
        hit = cache_hit_pct(m)
        hit_note = "good context reuse" if hit > 80 else "low cache reuse — context may be churning" if hit < 50 else "moderate"
        print(f"  Cache hit ratio:       {hit:.0f}%   ({hit_note})")
        out_in = m["output_tokens"] / max(1, m["input_tokens"] + m["cache_creation_input_tokens"])
        print(f"  Output / fresh input:  {out_in:.2f}x")
        if m["turns"] >= 4:
            recent = m["per_turn"][-3:]
            growing = all(recent[i]["in"] + recent[i]["cache_w"] >= recent[i-1]["in"] + recent[i-1]["cache_w"]
                          for i in range(1, len(recent)))
            if growing: print("  Trend:                 last 3 turns each grew (context bloat)")


def report_quick(jsonl: Path):
    records = list(parse_records(jsonl))
    m, subs, _ = collect(records)
    grand = main_thread_total(m) + sum(s["totalTokens"] for s in subs)
    total_cost = main_cost(m) + sum(subagent_cost(s) for s in subs)
    nt = new_tokens(m, subs)
    q = load_quota_cache() or {}
    sess = f"sess {q.get('session_pct')}%" if q.get("session_pct") is not None else "sess ?"
    week = f"week {q.get('week_all_pct')}%" if q.get("week_all_pct") is not None else "week ?"
    print(f"{fmt(grand)} tok ({fmt(nt)} new) · {fmt_dollars(total_cost)} · "
          f"{m['turns']} turns · subs {len(subs)} · cache {cache_hit_pct(m):.0f}% · {sess} · {week}")


def report_agents(jsonl: Path):
    records = list(parse_records(jsonl))
    _, subs, _ = collect(records)
    if not subs: print("(no subagent dispatches yet)"); return
    total = sum(s["totalTokens"] for s in subs)
    total_cost = sum(subagent_cost(s) for s in subs)
    print(f"## Subagents — {len(subs)} spawns, {fmt(total)} tok, {fmt_dollars(total_cost)}")
    for i, s in enumerate(subs, 1):
        c = subagent_cost(s)
        print(f"{i}. {s['agentType']} (assumed {s['assumed_model']})  {fmt(s['totalTokens'])} tok · {fmt_dollars(c)}  "
              f"({fmt(s['input_tokens'])} in / {fmt(s['output_tokens'])} out / {fmt(s['cache_read_input_tokens'])} cache-r)  "
              f"{s['durationMs']/1000:.1f}s · {s['toolUseCount']} tools")
        print(f"   \"{s['prompt_preview']}…\"")


def save_mark(name: str, jsonl: Path, capture_quota: bool = False):
    MARKS_DIR.mkdir(parents=True, exist_ok=True)
    size = jsonl.stat().st_size
    mark = {"name": name, "jsonl": str(jsonl), "byte_offset": size,
            "timestamp": time.time(), "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if capture_quota: mark["quota_at_mark"] = refresh_quota(force=True, jsonl=jsonl)
    (MARKS_DIR / f"{name}.json").write_text(json.dumps(mark, indent=2))
    print(f"Marked '{name}' at byte {size} in {jsonl.name}" + (" (+ quota snapshot)" if capture_quota else ""))


def load_mark(name: str):
    f = MARKS_DIR / f"{name}.json"
    if not f.exists():
        existing = [p.stem for p in MARKS_DIR.glob("*.json")] if MARKS_DIR.exists() else []
        print(f"ERR: no mark '{name}'. Existing: {existing or '(none)'}", file=sys.stderr); sys.exit(1)
    return json.loads(f.read_text())


def report_raw(jsonl: Path, byte_offset: int = 0):
    records = list(parse_records(jsonl, byte_offset))
    m, subs, tool_calls = collect(records)
    print(json.dumps({
        "source": str(jsonl),
        "config": load_config(),
        "main_thread": {**{k: m[k] for k in
                           ("turns", "input_tokens", "output_tokens",
                            "cache_read_input_tokens", "cache_creation_input_tokens")},
                        "tool_calls_issued": tool_calls,
                        "total_tokens": main_thread_total(m),
                        "cache_hit_pct": round(cache_hit_pct(m), 1),
                        "dollars_api_equiv": round(main_cost(m), 4),
                        "by_model": m["by_model"]},
        "subagents": [{**s, "dollars_api_equiv": round(subagent_cost(s), 4)} for s in subs],
        "subagent_total_tokens": sum(s["totalTokens"] for s in subs),
        "subagent_total_dollars": round(sum(subagent_cost(s) for s in subs), 4),
        "grand_total_tokens": main_thread_total(m) + sum(s["totalTokens"] for s in subs),
        "new_content_tokens": new_tokens(m, subs),
        "grand_total_dollars": round(main_cost(m) + sum(subagent_cost(s) for s in subs), 4),
        "quota": load_quota_cache(),
        "calibration": calibration_estimates(),
    }, indent=2, default=str))


def cmd_tier(args):
    cfg = load_config()
    if not args:
        cur = cfg.get("tier")
        if cur and cur in TIERS:
            ti = TIERS[cur]
            print(f"Current tier: {cur} ({ti['name']}, ${ti['monthly_fee_usd']}/mo)")
        else:
            print("No tier set. Run `usage2 tier <pro|max5x|max20x>`.")
            for k, v in TIERS.items():
                print(f"  {k:<8}  {v['name']:<10}  ${v['monthly_fee_usd']}/mo")
        return
    t = args[0].lower()
    if t not in TIERS:
        print(f"ERR: unknown tier '{t}'. Choose from: {list(TIERS.keys())}", file=sys.stderr); sys.exit(1)
    cfg["tier"] = t; save_config(cfg)
    ti = TIERS[t]
    print(f"Tier set to: {t} ({ti['name']}, ${ti['monthly_fee_usd']}/mo)")


def cmd_calibrate(args):
    include_crowd = "--crowd" in args
    cfg = load_config()
    tier = cfg.get("tier")
    reports = load_reports(include_crowd=include_crowd)
    print(f"## Calibration  ({len(reports)} reports{', tier filter=' + tier if tier else ''}{', incl. crowd' if include_crowd else ''})")
    print()
    own = [r for r in reports if not r.get("_crowd")]
    if own:
        print("### Own reports")
        for r in own[-10:]:
            ts = r.get("timestamp_local", r.get("timestamp_iso", "?"))
            p = r.get("panel", {})
            t5 = (r.get("trailing_5h") or {}).get("weighted_input_equiv", 0)
            print(f"  {ts}  tier={r.get('tier','?')}  sess {p.get('session_pct')}%  week {p.get('week_all_pct')}%  son {p.get('week_sonnet_pct')}%  ·  5h-w {fmt(t5)}")
        if len(own) > 10: print(f"  …({len(own) - 10} older)")
        print()
    crowd = [r for r in reports if r.get("_crowd")]
    if crowd:
        print(f"### Crowd reports ({len(crowd)})")
        by_tier = {}
        for r in crowd: by_tier.setdefault(r.get("tier", "?"), []).append(r)
        for t, rs in sorted(by_tier.items()):
            print(f"  {t}: {len(rs)} reports")
        print()
    est = calibration_estimates(include_crowd=include_crowd, tier_filter=tier)
    print("### Derived estimates" + (" (your tier only)" if tier else ""))
    for w in ("session", "week_all", "week_sonnet"):
        e = est.get(w)
        if e is None:
            print(f"  {w:<14}  (need ≥2 reports with delta)")
        else:
            print(f"  {w:<14}  {fmt(e['tokens_per_percent']):>10}/1%  ·  full ~{fmt(e['est_full_capacity'])}  ·  {e['slopes_used']} slopes from {e['samples']} samples")


def cmd_reset_calibration():
    """Archive all existing reports to reports_archive/<timestamp>/ for a fresh start."""
    if not REPORTS_DIR.exists() or not any(REPORTS_DIR.glob("*.json")):
        print("No reports to archive."); return
    archive_root = SKILL_DIR / "reports_archive"
    archive_root.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    target = archive_root / stamp
    target.mkdir()
    count = 0
    for f in REPORTS_DIR.glob("*.json"):
        f.rename(target / f.name)
        count += 1
    print(f"Archived {count} reports to {target}")
    print("Calibration is now empty. Run `usage2 sample` (with no concurrent agents for cleanest results) to start fresh.")


def cmd_reports():
    reports = load_reports(include_crowd=True)
    own = [r for r in reports if not r.get("_crowd")]
    crowd = [r for r in reports if r.get("_crowd")]
    print(f"Own reports:   {len(own)} in {REPORTS_DIR}")
    print(f"Crowd reports: {len(crowd)} in {CROWD_DIR}")
    if own:
        oldest = own[0].get("timestamp_local", "?")
        newest = own[-1].get("timestamp_local", "?")
        print(f"  Own range: {oldest} → {newest}")


def cmd_contribute(jsonl: Path):
    """Generate an anonymized report blob for sharing to the public repo."""
    reports = load_reports(include_crowd=False)
    if not reports:
        # Take a fresh one
        refresh_quota(force=True, jsonl=jsonl, write_report=True)
        reports = load_reports(include_crowd=False)
        if not reports:
            print("ERR: no reports to contribute. Run `usage2 quota` first.", file=sys.stderr); sys.exit(1)
    # Bundle: all of this user's reports, stripped of any identifying info beyond anon UUID + tier
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "contributed_at": datetime.now(timezone.utc).isoformat(),
        "report_count": len(reports),
        "reports": [{k: v for k, v in r.items() if k != "panel_text"} for r in reports],
    }
    print("## Anonymized contribution bundle")
    print("## To contribute: save this JSON as `crowd_reports/<your-anon-id>.json`")
    print("## in https://github.com/Loringtonian/usage2 and open a PR.")
    print()
    print(json.dumps(bundle, indent=2, default=str))


# ─────────────────────────── entry ───────────────────────────

def main():
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--")}
    pos = [a for a in args if not a.startswith("--")]
    mode = pos[0] if pos else "summary"
    rest = pos[1:]
    skip_quota = "--no-quota" in flags
    include_crowd = "--crowd" in flags

    if mode == "tier":
        cmd_tier(rest); return
    if mode == "calibrate":
        cmd_calibrate(args); return
    if mode == "reports":
        cmd_reports(); return
    if mode == "reset-calibration":
        cmd_reset_calibration(); return

    jsonl = current_session_jsonl()
    if not jsonl and mode not in ("marks", "drop", "quota", "sample"):
        print(f"ERR: no JSONL for cwd slug '{project_slug(Path.cwd())}'", file=sys.stderr); sys.exit(1)

    if mode in ("quota", "sample"):
        q = refresh_quota(force=True, write_report=True, jsonl=jsonl)
        if q:
            print(q.get("panel_text", ""))
            print("── parsed ──")
            print(f"session:     {q.get('session_pct')}%   resets {q.get('session_reset')}")
            print(f"week_all:    {q.get('week_all_pct')}%   resets {q.get('week_reset')}")
            print(f"week_sonnet: {q.get('week_sonnet_pct')}%   resets {q.get('week_sonnet_reset')}")
            latest = sorted(REPORTS_DIR.glob("*.json"))[-1] if REPORTS_DIR.exists() and any(REPORTS_DIR.glob("*.json")) else None
            if latest: print(f"\nReport saved: {latest.name}")
        else:
            print("ERR: capture failed", file=sys.stderr); sys.exit(1)
        return

    if mode == "contribute":
        cmd_contribute(jsonl); return

    if mode == "summary":
        report_summary(jsonl, skip_quota=skip_quota, include_crowd=include_crowd)
    elif mode == "quick":
        report_quick(jsonl)
    elif mode == "agents":
        report_agents(jsonl)
    elif mode == "raw":
        report_raw(jsonl)
    elif mode == "mark":
        if not rest: print("Usage: usage2 mark <name> [--quota]", file=sys.stderr); sys.exit(1)
        save_mark(rest[0], jsonl, capture_quota="--quota" in flags)
    elif mode == "since":
        if not rest: print("Usage: usage2 since <name>", file=sys.stderr); sys.exit(1)
        mk = load_mark(rest[0])
        report_summary(Path(mk["jsonl"]), byte_offset=mk["byte_offset"],
                       label=f"since '{mk['name']}' ({mk['iso_time']})", skip_quota=skip_quota,
                       include_crowd=include_crowd)
        if mk.get("quota_at_mark"):
            now_q = refresh_quota(jsonl=jsonl)
            print("### Quota Δ since mark")
            for key, label in (("session_pct", "Session"), ("week_all_pct", "Week (all)"), ("week_sonnet_pct", "Week (Sonnet)")):
                a = mk["quota_at_mark"].get(key); b = now_q.get(key) if now_q else None
                if a is not None and b is not None:
                    d = b - a; sign = "+" if d >= 0 else ""
                    print(f"  {label:<14}  {a}% → {b}%  ({sign}{d} pp)")
    elif mode == "marks":
        if not MARKS_DIR.exists(): print("(no marks)"); return
        for f in sorted(MARKS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
            d = json.loads(f.read_text())
            note = " [+quota]" if d.get("quota_at_mark") else ""
            print(f"  {d['name']:<24}  {d['iso_time']}  (byte {d['byte_offset']}){note}")
    elif mode == "drop":
        if not rest: print("Usage: usage2 drop <name>", file=sys.stderr); sys.exit(1)
        f = MARKS_DIR / f"{rest[0]}.json"
        if f.exists(): f.unlink(); print(f"Dropped '{rest[0]}'")
        else: print(f"No mark '{rest[0]}'", file=sys.stderr); sys.exit(1)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr); print(__doc__, file=sys.stderr); sys.exit(1)


if __name__ == "__main__":
    main()
