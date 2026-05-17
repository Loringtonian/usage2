#!/usr/bin/env python3
"""
usage2 meter — local token + cost + quota meter for Claude Code subscription users.

This skill is for people on a Claude subscription (Pro, Max 5x, Max 20x).
The dollar figures it reports are *API-equivalent* (what you would have paid
on metered API access). You actually pay the flat subscription fee.

Modes:
  summary           (default) full breakdown: tokens · $-equivalent · % of session/week
  quick             one-line totals (cheap to poll in a /loop)
  agents            per-subagent attribution only
  mark <name>       save a checkpoint at the current JSONL byte offset
  since <name>      report delta since a saved checkpoint (for A/B comparisons)
  marks             list saved checkpoints
  drop <name>       delete a checkpoint
  raw               dump aggregated counters as JSON
  quota             refresh + show rolling 5h / 7d / Sonnet-only quota (tmux scrape, ~12s)
  tier [<t>]        show/set subscription tier (pro | max5x | max20x)
  sample            take a calibration sample (token count + quota %) for future estimates
  calibrate         show calibration history + derived tokens-per-percent estimates
  estimate          estimate current session/week % from the calibration model
"""

import json
import os
import re
import sys
import time
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).parent
PROJECTS_DIR = Path.home() / ".claude" / "projects"
MARKS_DIR = SKILL_DIR / "marks"
CONFIG_FILE = SKILL_DIR / "config.json"
QUOTA_CACHE = SKILL_DIR / "quota_cache.json"
CALIBRATION_FILE = SKILL_DIR / "calibration.json"
CAPTURE_SH = SKILL_DIR / "capture.sh"

QUOTA_CACHE_TTL_SECONDS = 600  # 10 min

# Anthropic API rates ($ per million tokens). Update as Anthropic publishes new rates.
# 1h cache write is the dominant ephemeral category in our transcripts.
RATES = {
    "claude-opus-4-7":     {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write_5m": 18.75, "cache_write_1h": 30.00},
    "claude-opus-4-6":     {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write_5m": 18.75, "cache_write_1h": 30.00},
    "claude-sonnet-4-6":   {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m":  3.75, "cache_write_1h":  6.00},
    "claude-sonnet-4-5":   {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m":  3.75, "cache_write_1h":  6.00},
    "claude-haiku-4-5":    {"input":  0.80, "output":  4.00, "cache_read": 0.08, "cache_write_5m":  1.00, "cache_write_1h":  1.60},
}
# Fallback when we can't determine model (subagents). Sonnet is the default agent model.
DEFAULT_RATE_KEY = "claude-sonnet-4-6"

# Subagent type → assumed model for cost estimation.
AGENT_TYPE_MODEL = {
    "Explore":         "claude-haiku-4-5",
    "general-purpose": "claude-sonnet-4-6",
    "Plan":            "claude-sonnet-4-6",
    "code-reviewer":   "claude-sonnet-4-6",
}

# Subscription tier metadata. Monthly_fee_usd is the headline price (for the
# value-context line). Anthropic doesn't publish exact token caps, so we don't
# claim them — calibration learns them empirically from /usage panel samples.
TIERS = {
    "pro":     {"name": "Pro",        "monthly_fee_usd":  20},
    "max5x":   {"name": "Max 5x",     "monthly_fee_usd": 100},
    "max20x":  {"name": "Max 20x",    "monthly_fee_usd": 200},
}


# ─────────────────────────── transcript parsing ───────────────────────────

def project_slug(path: Path) -> str:
    return re.sub(r"[/_]", "-", str(path.resolve()).rstrip("/"))


def current_session_jsonl(cwd: Path | None = None) -> Path | None:
    cwd = cwd or Path.cwd()
    project_dir = PROJECTS_DIR / project_slug(cwd)
    if not project_dir.exists():
        return None
    files = list(project_dir.glob("*.jsonl"))
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def parse_records(path: Path, byte_offset: int = 0):
    with open(path, "rb") as f:
        if byte_offset:
            f.seek(byte_offset)
            f.readline()  # skip partial line
        for raw in f:
            try:
                yield json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue


def collect(records):
    main = {
        "turns": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        "ephemeral_1h": 0, "ephemeral_5m": 0,
        "per_turn": [],
        "by_model": {},  # model → {input, output, cache_read, cache_write_5m, cache_write_1h}
    }
    subagents = []
    tool_call_count = 0

    for r in records:
        rtype = r.get("type")
        ts = r.get("timestamp")

        if rtype == "assistant" and not r.get("isSidechain"):
            msg = r.get("message") or {}
            usage = msg.get("usage")
            model = msg.get("model") or "unknown"
            if usage:
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
                assumed_model = AGENT_TYPE_MODEL.get(tur.get("agentType"), DEFAULT_RATE_KEY)
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
                    "assumed_model": assumed_model,
                })

    return main, subagents, tool_call_count


# ─────────────────────────── cost calculation ───────────────────────────

def rates_for(model: str) -> dict:
    return RATES.get(model, RATES[DEFAULT_RATE_KEY])


def cost_of(usage_breakdown: dict, model: str) -> float:
    """Dollar cost given a usage breakdown {input,output,cache_read,cache_write_5m,cache_write_1h}."""
    r = rates_for(model)
    return (
        usage_breakdown.get("input", 0) * r["input"] / 1_000_000
        + usage_breakdown.get("output", 0) * r["output"] / 1_000_000
        + usage_breakdown.get("cache_read", 0) * r["cache_read"] / 1_000_000
        + usage_breakdown.get("cache_write_5m", 0) * r["cache_write_5m"] / 1_000_000
        + usage_breakdown.get("cache_write_1h", 0) * r["cache_write_1h"] / 1_000_000
    )


def main_cost(m: dict) -> float:
    return sum(cost_of(bm, model) for model, bm in m["by_model"].items())


def subagent_cost(s: dict) -> float:
    return cost_of({
        "input": s["input_tokens"],
        "output": s["output_tokens"],
        "cache_read": s["cache_read_input_tokens"],
        "cache_write_5m": s["ephemeral_5m"],
        "cache_write_1h": s["ephemeral_1h"],
    }, s["assumed_model"])


# ─────────────────────────── config / quota / calibration storage ───────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try: return json.loads(CONFIG_FILE.read_text())
        except: pass
    return {}


def save_config(cfg: dict):
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def load_quota_cache() -> dict | None:
    if QUOTA_CACHE.exists():
        try: return json.loads(QUOTA_CACHE.read_text())
        except: pass
    return None


def save_quota_cache(data: dict):
    QUOTA_CACHE.write_text(json.dumps(data, indent=2))


def load_calibration() -> dict:
    if CALIBRATION_FILE.exists():
        try: return json.loads(CALIBRATION_FILE.read_text())
        except: pass
    return {"samples": []}


def save_calibration(cal: dict):
    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2))


# ─────────────────────────── quota panel capture + parse ───────────────────────────

def capture_quota_panel() -> str:
    """Run capture.sh and return its stdout."""
    result = subprocess.run(["bash", str(CAPTURE_SH)], capture_output=True, text=True, timeout=60)
    return result.stdout


def parse_quota_panel(text: str) -> dict:
    """Parse the /usage panel output for percentages and reset times."""
    out = {
        "session_pct": None, "session_reset": None,
        "week_all_pct": None, "week_reset": None,
        "week_sonnet_pct": None, "week_sonnet_reset": None,
    }
    section = None
    for line in text.splitlines():
        s = line.strip()
        if "Current session" in s:
            section = "session"
        elif "Current week (all models)" in s:
            section = "week_all"
        elif "Current week (Sonnet only)" in s:
            section = "week_sonnet"
        elif s.startswith("What's contributing") or s.startswith("Extra usage"):
            section = None

        m = re.search(r"(\d+)%\s*used", s)
        if m and section:
            key = f"{section}_pct"
            if out.get(key) is None:
                out[key] = int(m.group(1))

        m2 = re.search(r"Resets\s+(.+?)\s*$", s)
        if m2 and section:
            key = "session_reset" if section == "session" else "week_reset" if section == "week_all" else "week_sonnet_reset"
            if out.get(key) is None:
                out[key] = m2.group(1).strip()

    return out


def refresh_quota_if_stale(force: bool = False) -> dict | None:
    """Returns the cached or freshly-captured quota panel data."""
    cache = load_quota_cache()
    if cache and not force:
        age = time.time() - cache.get("ts", 0)
        if age < QUOTA_CACHE_TTL_SECONDS:
            return cache
    try:
        panel_text = capture_quota_panel()
        parsed = parse_quota_panel(panel_text)
        parsed["ts"] = time.time()
        parsed["panel_text"] = panel_text
        save_quota_cache(parsed)
        return parsed
    except Exception as e:
        if cache:
            return cache  # stale is better than nothing
        return None


# ─────────────────────────── calibration: tokens-per-percent ───────────────────────────

def tokens_in_window(jsonl: Path, window_seconds: float) -> dict:
    """Sum token usage from the transcript within the trailing window."""
    cutoff = time.time() - window_seconds
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
              "raw_total": 0, "weighted": 0.0, "dollars": 0.0}

    def ts_seconds(ts_str):
        if not ts_str: return 0
        try:
            from datetime import datetime
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        except: return 0

    for r in parse_records(jsonl):
        rtype = r.get("type")
        ts = ts_seconds(r.get("timestamp"))
        if ts < cutoff:
            continue

        if rtype == "assistant" and not r.get("isSidechain"):
            msg = r.get("message") or {}
            usage = msg.get("usage")
            model = msg.get("model") or "unknown"
            if usage:
                in_t = usage.get("input_tokens", 0) or 0
                out_t = usage.get("output_tokens", 0) or 0
                cr_t = usage.get("cache_read_input_tokens", 0) or 0
                cw_t = usage.get("cache_creation_input_tokens", 0) or 0
                cc = usage.get("cache_creation") or {}
                e5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
                e1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
                totals["input"] += in_t
                totals["output"] += out_t
                totals["cache_read"] += cr_t
                totals["cache_write"] += cw_t
                totals["raw_total"] += in_t + out_t + cr_t + cw_t
                # Weighted by API-rate ratios (input-equivalent units)
                r_ = rates_for(model)
                totals["weighted"] += in_t + out_t * (r_["output"]/r_["input"]) \
                                     + cr_t * (r_["cache_read"]/r_["input"]) \
                                     + e5m * (r_["cache_write_5m"]/r_["input"]) \
                                     + e1h * (r_["cache_write_1h"]/r_["input"])
                totals["dollars"] += cost_of({"input": in_t, "output": out_t, "cache_read": cr_t,
                                              "cache_write_5m": e5m, "cache_write_1h": e1h}, model)

        if rtype == "user":
            tur = r.get("toolUseResult")
            if isinstance(tur, dict) and tur.get("agentType"):
                u = tur.get("usage") or {}
                cc = u.get("cache_creation") or {}
                in_t = u.get("input_tokens", 0) or 0
                out_t = u.get("output_tokens", 0) or 0
                cr_t = u.get("cache_read_input_tokens", 0) or 0
                cw_t = u.get("cache_creation_input_tokens", 0) or 0
                e5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
                e1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
                assumed = AGENT_TYPE_MODEL.get(tur.get("agentType"), DEFAULT_RATE_KEY)
                r_ = rates_for(assumed)
                totals["input"] += in_t
                totals["output"] += out_t
                totals["cache_read"] += cr_t
                totals["cache_write"] += cw_t
                totals["raw_total"] += in_t + out_t + cr_t + cw_t
                totals["weighted"] += in_t + out_t * (r_["output"]/r_["input"]) \
                                     + cr_t * (r_["cache_read"]/r_["input"]) \
                                     + e5m * (r_["cache_write_5m"]/r_["input"]) \
                                     + e1h * (r_["cache_write_1h"]/r_["input"])
                totals["dollars"] += cost_of({"input": in_t, "output": out_t, "cache_read": cr_t,
                                              "cache_write_5m": e5m, "cache_write_1h": e1h}, assumed)

    return totals


def take_calibration_sample(jsonl: Path) -> dict:
    """Take a fresh quota sample + record the trailing-window token totals."""
    quota = refresh_quota_if_stale(force=True)
    if not quota:
        raise RuntimeError("Could not capture /usage panel")
    window_5h = tokens_in_window(jsonl, 5 * 3600)
    window_7d = tokens_in_window(jsonl, 7 * 86400)
    sample = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_pct": quota.get("session_pct"),
        "week_all_pct": quota.get("week_all_pct"),
        "week_sonnet_pct": quota.get("week_sonnet_pct"),
        "tokens_5h": window_5h,
        "tokens_7d": window_7d,
    }
    cal = load_calibration()
    cal["samples"].append(sample)
    # Keep last 50 samples (more than enough; older are stale anyway)
    cal["samples"] = cal["samples"][-50:]
    save_calibration(cal)
    return sample


def calibration_estimates() -> dict:
    """Compute tokens-per-percent from sample history. Returns per-window estimates."""
    cal = load_calibration()
    samples = cal.get("samples", [])
    out = {"samples": len(samples), "session": None, "week_all": None, "week_sonnet": None}
    if len(samples) < 2:
        return out

    def estimate(key_pct, key_tokens, weighted=True):
        # For each pair of samples (within the relevant window), compute slope
        points = [(s[key_pct], s[key_tokens]["weighted" if weighted else "raw_total"])
                  for s in samples if s.get(key_pct) is not None]
        if len(points) < 2:
            return None
        # Use simplest estimator: (delta tokens) / (delta pct) across consecutive samples
        slopes = []
        for i in range(1, len(points)):
            dp = points[i][0] - points[i-1][0]
            dt = points[i][1] - points[i-1][1]
            if dp > 0 and dt > 0:
                slopes.append(dt / dp)
        if not slopes:
            # Fall back to single-sample estimate: tokens_in_window / pct
            for pct, tokens in points:
                if pct > 0:
                    slopes.append(tokens / pct)
        if not slopes:
            return None
        median_slope = sorted(slopes)[len(slopes) // 2]
        return {
            "tokens_per_percent": median_slope,
            "est_full_capacity": median_slope * 100,
            "samples_used": len(points),
            "all_slopes": slopes,
        }

    out["session"] = estimate("session_pct", "tokens_5h")
    out["week_all"] = estimate("week_all_pct", "tokens_7d")
    out["week_sonnet"] = estimate("week_sonnet_pct", "tokens_7d")
    return out


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


def main_thread_total(m):
    return m["input_tokens"] + m["output_tokens"] + m["cache_read_input_tokens"] + m["cache_creation_input_tokens"]


def cache_hit_pct(m):
    seen = m["cache_read_input_tokens"] + m["cache_creation_input_tokens"] + m["input_tokens"]
    return (m["cache_read_input_tokens"] / seen * 100) if seen else 0.0


# ─────────────────────────── reports ───────────────────────────

def quota_block(skip_refresh: bool = False) -> tuple[str, dict | None]:
    """Return a multi-line string for the quota section + the parsed quota data."""
    quota = refresh_quota_if_stale() if not skip_refresh else load_quota_cache()
    if not quota:
        return "  (quota panel unavailable — run `usage2 quota` to refresh)", None
    age = time.time() - quota.get("ts", 0)
    age_str = f"{int(age)}s ago" if age < 90 else f"{int(age/60)}min ago"
    lines = []
    if quota.get("session_pct") is not None:
        lines.append(f"  Session (5h window):  {quota['session_pct']:3d}%   resets {quota.get('session_reset', '?')}")
    if quota.get("week_all_pct") is not None:
        lines.append(f"  Week (all models):    {quota['week_all_pct']:3d}%   resets {quota.get('week_reset', '?')}")
    if quota.get("week_sonnet_pct") is not None:
        lines.append(f"  Week (Sonnet only):   {quota['week_sonnet_pct']:3d}%   resets {quota.get('week_sonnet_reset', '?')}")
    lines.append(f"  ({age_str})")
    return "\n".join(lines), quota


def report_summary(jsonl: Path, byte_offset: int = 0, label: str = "", skip_quota: bool = False):
    records = list(parse_records(jsonl, byte_offset))
    m, subs, tool_calls = collect(records)
    main_total = main_thread_total(m)
    sub_total = sum(s["totalTokens"] for s in subs)
    grand = main_total + sub_total
    m_cost = main_cost(m)
    s_cost = sum(subagent_cost(s) for s in subs)
    total_cost = m_cost + s_cost

    cfg = load_config()
    tier = cfg.get("tier")
    tier_info = TIERS.get(tier) if tier else None

    print(f"## Token usage{(' — ' + label) if label else ''}")
    print(f"## Source: {jsonl}")
    print()

    print("### Main thread")
    if m["turns"] == 0:
        print("  (no completed turns yet in this range)")
    else:
        print(f"  Turns:              {m['turns']}")
        print(f"  Tool calls issued:  {tool_calls}")
        print(f"  Input (fresh):      {fmt(m['input_tokens'])}")
        print(f"  Output:             {fmt(m['output_tokens'])}")
        print(f"  Cache read:         {fmt(m['cache_read_input_tokens'])}  ({cache_hit_pct(m):.0f}% of all input)")
        print(f"  Cache write:        {fmt(m['cache_creation_input_tokens'])}  (1h: {fmt(m['ephemeral_1h'])} · 5m: {fmt(m['ephemeral_5m'])})")
        if len(m["by_model"]) > 1:
            for mdl, bm in sorted(m["by_model"].items(), key=lambda kv: -sum(kv[1].values()) + kv[1]["turns"]):
                print(f"    {mdl}: {bm['turns']} turns · {fmt(bm['input']+bm['output']+bm['cache_read']+bm['cache_write_5m']+bm['cache_write_1h'])}")
        print(f"  Main-thread total:  {fmt(main_total)}  ·  {fmt_dollars(m_cost)} API-equiv")
        if m["turns"] >= 2:
            avg_in = sum(t["in"] + t["cache_w"] for t in m["per_turn"]) // m["turns"]
            avg_out = sum(t["out"] for t in m["per_turn"]) // m["turns"]
            print(f"  Avg per turn:       in {fmt(avg_in)}, out {fmt(avg_out)}")
    print()

    if subs:
        print(f"### Subagents ({len(subs)} spawn{'s' if len(subs) != 1 else ''}, {fmt(sub_total)} tokens, {fmt_dollars(s_cost)})")
        by_type = {}
        for s in subs:
            by_type.setdefault(s["agentType"], []).append(s)
        for t in sorted(by_type, key=lambda k: -sum(s["totalTokens"] for s in by_type[k])):
            spawns = by_type[t]
            tt = sum(s["totalTokens"] for s in spawns)
            tc = sum(subagent_cost(s) for s in spawns)
            print(f"  {t} × {len(spawns)} (assumed {AGENT_TYPE_MODEL.get(t, 'sonnet')}): {fmt(tt)} · {fmt_dollars(tc)}")
            for s in spawns:
                pp = s["prompt_preview"][:80]
                print(
                    f"    └─ {fmt(s['totalTokens']):>6}  {fmt_dollars(subagent_cost(s)):>7}  "
                    f"in {fmt(s['input_tokens'])}  out {fmt(s['output_tokens'])}  "
                    f"cache-r {fmt(s['cache_read_input_tokens'])}  "
                    f"{s['durationMs'] / 1000:.1f}s  {s['toolUseCount']} tools  \"{pp}…\""
                )
        print()

    print(f"### Grand total: {fmt(grand)} tokens · {fmt_dollars(total_cost)} API-equiv")
    if tier_info:
        days_of_fee = total_cost / (tier_info["monthly_fee_usd"] / 30) if tier_info["monthly_fee_usd"] else 0
        print(f"### Tier: {tier_info['name']} (${tier_info['monthly_fee_usd']}/mo)  →  this session = {days_of_fee:.1f} days of your subscription fee in API-equivalent value")
    print()

    if not skip_quota:
        print("### Rolling quota windows (from /usage panel)")
        qblock, quota = quota_block()
        print(qblock)
        print()

    # Calibration-derived estimates
    cal_est = calibration_estimates()
    if cal_est["samples"] >= 2:
        print(f"### Calibration ({cal_est['samples']} samples in history)")
        if cal_est["session"]:
            cap = cal_est["session"]["est_full_capacity"]
            print(f"  Session 5h capacity:    ~{fmt(cap)} weighted-input-equiv tokens  ({fmt(cal_est['session']['tokens_per_percent'])} per 1%)")
        if cal_est["week_all"]:
            cap = cal_est["week_all"]["est_full_capacity"]
            print(f"  Week 7d capacity:       ~{fmt(cap)} weighted-input-equiv tokens  ({fmt(cal_est['week_all']['tokens_per_percent'])} per 1%)")
        if cal_est["week_sonnet"]:
            cap = cal_est["week_sonnet"]["est_full_capacity"]
            print(f"  Week Sonnet capacity:   ~{fmt(cap)} weighted-input-equiv tokens  ({fmt(cal_est['week_sonnet']['tokens_per_percent'])} per 1%)")
        print()
    elif cal_est["samples"] == 1:
        print(f"### Calibration: 1 sample (need ≥2 for slope estimate — run `usage2 sample` again later)")
        print()
    else:
        print(f"### Calibration: no samples yet — run `usage2 sample` once now and again later to learn your tier's tokens-per-percent")
        print()

    if m["turns"] >= 3:
        print("### Efficiency signals")
        hit = cache_hit_pct(m)
        hit_note = "good context reuse" if hit > 80 else "low cache reuse — context may be churning" if hit < 50 else "moderate"
        print(f"  Cache hit ratio:       {hit:.0f}%   ({hit_note})")
        out_in = (m["output_tokens"] / max(1, m["input_tokens"] + m["cache_creation_input_tokens"]))
        print(f"  Output / fresh input:  {out_in:.2f}x")
        if m["turns"] >= 4:
            recent = m["per_turn"][-3:]
            growing = all(recent[i]["in"] + recent[i]["cache_w"] >= recent[i - 1]["in"] + recent[i - 1]["cache_w"] for i in range(1, len(recent)))
            if growing:
                print("  Trend:                 last 3 turns each grew (context bloat)")


def report_quick(jsonl: Path):
    records = list(parse_records(jsonl))
    m, subs, _ = collect(records)
    main_total = main_thread_total(m)
    sub_total = sum(s["totalTokens"] for s in subs)
    total_cost = main_cost(m) + sum(subagent_cost(s) for s in subs)
    quota = load_quota_cache() or {}
    sess = f"sess {quota.get('session_pct')}%" if quota.get("session_pct") is not None else "sess ?%"
    week = f"week {quota.get('week_all_pct')}%" if quota.get("week_all_pct") is not None else "week ?%"
    print(
        f"{fmt(main_total + sub_total)} tok · {fmt_dollars(total_cost)} API · "
        f"main {fmt(main_total)} ({m['turns']} turns) · subs {fmt(sub_total)} ({len(subs)}) · "
        f"cache {cache_hit_pct(m):.0f}% · {sess} · {week}"
    )


def report_agents(jsonl: Path):
    records = list(parse_records(jsonl))
    _, subs, _ = collect(records)
    if not subs:
        print("(no subagent dispatches in this session yet)")
        return
    total = sum(s["totalTokens"] for s in subs)
    total_cost = sum(subagent_cost(s) for s in subs)
    print(f"## Subagent attribution — {len(subs)} spawns, {fmt(total)} tokens, {fmt_dollars(total_cost)}")
    print()
    for i, s in enumerate(subs, 1):
        c = subagent_cost(s)
        print(
            f"{i}. {s['agentType']} (assumed {s['assumed_model']})  {fmt(s['totalTokens'])} tok · {fmt_dollars(c)}  "
            f"(in {fmt(s['input_tokens'])} / out {fmt(s['output_tokens'])} / cache-r {fmt(s['cache_read_input_tokens'])})  "
            f"{s['durationMs'] / 1000:.1f}s · {s['toolUseCount']} tools · {s['status']}"
        )
        print(f"   \"{s['prompt_preview']}…\"")


def save_mark(name: str, jsonl: Path, capture_quota: bool = False):
    MARKS_DIR.mkdir(parents=True, exist_ok=True)
    size = jsonl.stat().st_size
    mark = {
        "name": name, "jsonl": str(jsonl),
        "byte_offset": size, "timestamp": time.time(),
        "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if capture_quota:
        q = refresh_quota_if_stale(force=True)
        mark["quota_at_mark"] = q
    (MARKS_DIR / f"{name}.json").write_text(json.dumps(mark, indent=2))
    print(f"Marked '{name}' at byte {size} in {jsonl.name}"
          + (" (with quota snapshot)" if capture_quota else ""))


def load_mark(name: str):
    f = MARKS_DIR / f"{name}.json"
    if not f.exists():
        existing = [p.stem for p in MARKS_DIR.glob("*.json")] if MARKS_DIR.exists() else []
        print(f"ERR: no mark named '{name}'. Existing: {existing or '(none)'}", file=sys.stderr)
        sys.exit(1)
    return json.loads(f.read_text())


def report_raw(jsonl: Path, byte_offset: int = 0):
    records = list(parse_records(jsonl, byte_offset))
    m, subs, tool_calls = collect(records)
    quota = load_quota_cache()
    cfg = load_config()
    cal_est = calibration_estimates()
    out = {
        "source": str(jsonl),
        "config": cfg,
        "main_thread": {
            "turns": m["turns"],
            "tool_calls_issued": tool_calls,
            "input_tokens": m["input_tokens"],
            "output_tokens": m["output_tokens"],
            "cache_read_input_tokens": m["cache_read_input_tokens"],
            "cache_creation_input_tokens": m["cache_creation_input_tokens"],
            "total_tokens": main_thread_total(m),
            "cache_hit_pct": round(cache_hit_pct(m), 1),
            "dollars_api_equiv": round(main_cost(m), 4),
            "by_model": m["by_model"],
        },
        "subagents": [{**s, "dollars_api_equiv": round(subagent_cost(s), 4)} for s in subs],
        "subagent_total_tokens": sum(s["totalTokens"] for s in subs),
        "subagent_total_dollars": round(sum(subagent_cost(s) for s in subs), 4),
        "grand_total_tokens": main_thread_total(m) + sum(s["totalTokens"] for s in subs),
        "grand_total_dollars": round(main_cost(m) + sum(subagent_cost(s) for s in subs), 4),
        "quota": quota,
        "calibration": cal_est,
    }
    print(json.dumps(out, indent=2, default=str))


# ─────────────────────────── entry point ───────────────────────────

def cmd_tier(args):
    cfg = load_config()
    if not args:
        cur = cfg.get("tier")
        if cur and cur in TIERS:
            ti = TIERS[cur]
            print(f"Current tier: {cur} ({ti['name']}, ${ti['monthly_fee_usd']}/mo)")
        else:
            print("No tier set. Run `usage2 tier <pro|max5x|max20x>` to set.")
            print("Available tiers:")
            for k, v in TIERS.items():
                print(f"  {k:<8}  {v['name']:<10}  ${v['monthly_fee_usd']}/mo")
        return
    t = args[0].lower()
    if t not in TIERS:
        print(f"ERR: unknown tier '{t}'. Choose from: {list(TIERS.keys())}", file=sys.stderr)
        sys.exit(1)
    cfg["tier"] = t
    save_config(cfg)
    ti = TIERS[t]
    print(f"Tier set to: {t} ({ti['name']}, ${ti['monthly_fee_usd']}/mo)")


def cmd_calibrate():
    cal = load_calibration()
    samples = cal.get("samples", [])
    if not samples:
        print("No calibration samples yet. Run `usage2 sample` to record one.")
        return
    print(f"## Calibration history ({len(samples)} samples)")
    print()
    for s in samples:
        sp = s.get("session_pct")
        wp = s.get("week_all_pct")
        ws = s.get("week_sonnet_pct")
        t5 = s.get("tokens_5h", {}).get("weighted", 0)
        t7 = s.get("tokens_7d", {}).get("weighted", 0)
        print(f"  {s['iso']}  session {sp}%  week {wp}%  sonnet-week {ws}%  ·  5h-tokens {fmt(t5)}  7d-tokens {fmt(t7)}")
    print()
    print("## Derived estimates")
    est = calibration_estimates()
    for w in ("session", "week_all", "week_sonnet"):
        e = est.get(w)
        if e is None:
            print(f"  {w:<14}  (need ≥2 samples)")
        else:
            print(f"  {w:<14}  {fmt(e['tokens_per_percent'])} tokens / 1%  →  full capacity ~{fmt(e['est_full_capacity'])} weighted-input-equiv")


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "summary"
    rest = args[1:]

    if mode in ("tier",):
        cmd_tier(rest)
        return
    if mode == "calibrate":
        cmd_calibrate()
        return

    if mode == "quota":
        q = refresh_quota_if_stale(force=True)
        if q:
            print(q.get("panel_text", "(no panel text)"))
            print()
            print("── parsed ──")
            print(f"session: {q.get('session_pct')}%   resets {q.get('session_reset')}")
            print(f"week_all: {q.get('week_all_pct')}%   resets {q.get('week_reset')}")
            print(f"week_sonnet: {q.get('week_sonnet_pct')}%   resets {q.get('week_sonnet_reset')}")
        else:
            print("ERR: could not capture /usage panel", file=sys.stderr)
            sys.exit(1)
        return

    jsonl = current_session_jsonl()
    if not jsonl and mode not in ("marks", "drop"):
        slug = project_slug(Path.cwd())
        print(f"ERR: no JSONL found for project slug '{slug}'", file=sys.stderr)
        print(f"    expected dir: {PROJECTS_DIR / slug}", file=sys.stderr)
        sys.exit(1)

    if mode == "summary":
        report_summary(jsonl)
    elif mode == "summary-fast":
        report_summary(jsonl, skip_quota=True)
    elif mode == "quick":
        report_quick(jsonl)
    elif mode == "agents":
        report_agents(jsonl)
    elif mode == "raw":
        report_raw(jsonl)
    elif mode == "sample":
        s = take_calibration_sample(jsonl)
        print(f"Sample recorded at {s['iso']}: session {s['session_pct']}%, week {s['week_all_pct']}%, sonnet-week {s['week_sonnet_pct']}%")
        print(f"  trailing-5h weighted-input-equiv: {fmt(s['tokens_5h']['weighted'])}")
        print(f"  trailing-7d weighted-input-equiv: {fmt(s['tokens_7d']['weighted'])}")
        est = calibration_estimates()
        if est["samples"] >= 2:
            print(f"  Now {est['samples']} samples — derived estimates available via `usage2 calibrate`.")
        else:
            print("  Run `usage2 sample` again later (15+ min apart) to derive slopes.")
    elif mode == "mark":
        if len(rest) < 1:
            print("Usage: usage2 mark <name> [--quota]", file=sys.stderr); sys.exit(1)
        save_mark(rest[0], jsonl, capture_quota=("--quota" in rest))
    elif mode == "since":
        if len(rest) < 1:
            print("Usage: usage2 since <name>", file=sys.stderr); sys.exit(1)
        mk = load_mark(rest[0])
        report_summary(Path(mk["jsonl"]), byte_offset=mk["byte_offset"],
                       label=f"since '{mk['name']}' ({mk['iso_time']})")
        if mk.get("quota_at_mark"):
            print()
            print("### Quota delta since mark")
            now_q = refresh_quota_if_stale()
            mk_q = mk["quota_at_mark"]
            for key, label in [("session_pct", "Session"), ("week_all_pct", "Week (all)"), ("week_sonnet_pct", "Week (Sonnet)")]:
                a = mk_q.get(key); b = now_q.get(key) if now_q else None
                if a is not None and b is not None:
                    delta = b - a
                    sign = "+" if delta >= 0 else ""
                    print(f"  {label:<14}  {a}% → {b}%  ({sign}{delta} pp)")
    elif mode == "marks":
        if not MARKS_DIR.exists():
            print("(no marks saved)"); return
        for m in sorted(MARKS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
            d = json.loads(m.read_text())
            quota_note = " [w/quota]" if d.get("quota_at_mark") else ""
            print(f"  {d['name']:<24}  {d['iso_time']}  (byte {d['byte_offset']}){quota_note}")
    elif mode == "drop":
        if len(rest) < 1:
            print("Usage: usage2 drop <name>", file=sys.stderr); sys.exit(1)
        f = MARKS_DIR / f"{rest[0]}.json"
        if f.exists():
            f.unlink(); print(f"Dropped '{rest[0]}'")
        else:
            print(f"No mark '{rest[0]}'", file=sys.stderr); sys.exit(1)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
