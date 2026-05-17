#!/usr/bin/env python3
"""
usage2 meter — read the local Claude Code transcript JSONL and report
authoritative per-action token usage.

Modes:
  summary          (default) full session breakdown: main + subagents + signals
  quick            one-line totals (cheap to call in a loop)
  mark <name>      save a checkpoint at the current JSONL byte offset
  since <name>     report delta since a saved checkpoint (for A/B comparisons)
  marks            list all saved checkpoints
  drop <name>      delete a saved checkpoint
  agents           per-subagent attribution only
  raw              dump aggregated counters as JSON (for downstream tools)

This reports tokens the LLM actually consumed, drawn from the `usage` blocks
that Claude Code writes to ~/.claude/projects/<slug>/<session>.jsonl as the
API responds. Includes subagent (Task tool) attribution via toolUseResult
records. Does not include the in-progress turn (that record isn't written
until the turn completes).
"""

import json
import os
import re
import sys
import time
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
MARKS_DIR = Path(__file__).parent / "marks"


def project_slug(path: Path) -> str:
    return re.sub(r"[/_]", "-", str(path.resolve()).rstrip("/"))


def current_session_jsonl(cwd: Path | None = None) -> Path | None:
    cwd = cwd or Path.cwd()
    slug = project_slug(cwd)
    project_dir = PROJECTS_DIR / slug
    if not project_dir.exists():
        return None
    files = list(project_dir.glob("*.jsonl"))
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def parse_records(path: Path, byte_offset: int = 0):
    with open(path, "rb") as f:
        if byte_offset:
            f.seek(byte_offset)
            # If we landed mid-line, skip to next newline
            if byte_offset > 0:
                f.readline()
        for raw in f:
            try:
                yield json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue


def collect(records):
    main = {
        "turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "ephemeral_1h": 0,
        "ephemeral_5m": 0,
        "per_turn": [],  # list of {idx, in, out, cache_r, cache_w}
    }
    subagents = []
    tool_call_count = 0

    for r in records:
        rtype = r.get("type")

        if rtype == "assistant" and not r.get("isSidechain"):
            msg = r.get("message") or {}
            usage = msg.get("usage")
            if usage:
                main["turns"] += 1
                in_t = usage.get("input_tokens", 0) or 0
                out_t = usage.get("output_tokens", 0) or 0
                cr_t = usage.get("cache_read_input_tokens", 0) or 0
                cw_t = usage.get("cache_creation_input_tokens", 0) or 0
                main["input_tokens"] += in_t
                main["output_tokens"] += out_t
                main["cache_read_input_tokens"] += cr_t
                main["cache_creation_input_tokens"] += cw_t
                cc = usage.get("cache_creation") or {}
                main["ephemeral_1h"] += cc.get("ephemeral_1h_input_tokens", 0) or 0
                main["ephemeral_5m"] += cc.get("ephemeral_5m_input_tokens", 0) or 0
                main["per_turn"].append(
                    {"in": in_t, "out": out_t, "cache_r": cr_t, "cache_w": cw_t}
                )
                # Count tool_use blocks in this assistant message
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_call_count += 1

        if rtype == "user":
            tur = r.get("toolUseResult")
            if isinstance(tur, dict) and tur.get("agentType"):
                u = tur.get("usage") or {}
                subagents.append(
                    {
                        "agentType": tur.get("agentType") or "unknown",
                        "agentName": tur.get("agentName"),
                        "totalTokens": tur.get("totalTokens", 0) or 0,
                        "input_tokens": u.get("input_tokens", 0) or 0,
                        "output_tokens": u.get("output_tokens", 0) or 0,
                        "cache_read_input_tokens": u.get("cache_read_input_tokens", 0) or 0,
                        "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0) or 0,
                        "durationMs": tur.get("totalDurationMs", 0) or 0,
                        "toolUseCount": tur.get("totalToolUseCount", 0) or 0,
                        "prompt_preview": ((tur.get("prompt") or "")[:120]).replace("\n", " "),
                        "status": tur.get("status"),
                    }
                )

    return main, subagents, tool_call_count


def fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def main_thread_total(m):
    return m["input_tokens"] + m["output_tokens"] + m["cache_read_input_tokens"] + m["cache_creation_input_tokens"]


def cache_hit_pct(m):
    seen = m["cache_read_input_tokens"] + m["cache_creation_input_tokens"] + m["input_tokens"]
    return (m["cache_read_input_tokens"] / seen * 100) if seen else 0.0


def report_summary(jsonl: Path, byte_offset: int = 0, label: str = ""):
    records = list(parse_records(jsonl, byte_offset))
    m, subs, tool_calls = collect(records)
    main_total = main_thread_total(m)
    sub_total = sum(s["totalTokens"] for s in subs)
    grand = main_total + sub_total

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
        print(f"  Main-thread total:  {fmt(main_total)}")
        if m["turns"] >= 2:
            avg_in = sum(t["in"] + t["cache_w"] for t in m["per_turn"]) // m["turns"]
            avg_out = sum(t["out"] for t in m["per_turn"]) // m["turns"]
            print(f"  Avg per turn:       in {fmt(avg_in)}, out {fmt(avg_out)}")
    print()

    if subs:
        print(f"### Subagents ({len(subs)} spawn{'s' if len(subs) != 1 else ''}, {fmt(sub_total)} tokens)")
        by_type = {}
        for s in subs:
            by_type.setdefault(s["agentType"], []).append(s)
        for t in sorted(by_type, key=lambda k: -sum(s["totalTokens"] for s in by_type[k])):
            spawns = by_type[t]
            tt = sum(s["totalTokens"] for s in spawns)
            print(f"  {t} × {len(spawns)}: {fmt(tt)}")
            for s in spawns:
                pp = s["prompt_preview"][:80]
                print(
                    f"    └─ {fmt(s['totalTokens']):>6}  in {fmt(s['input_tokens'])}  out {fmt(s['output_tokens'])}  "
                    f"cache-r {fmt(s['cache_read_input_tokens'])}  "
                    f"{s['durationMs'] / 1000:.1f}s  {s['toolUseCount']} tools  \"{pp}…\""
                )
        print()

    print(f"### Grand total: {fmt(grand)} tokens")
    if m["turns"] >= 3:
        print()
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
    print(
        f"{fmt(main_total + sub_total)} total · main {fmt(main_total)} ({m['turns']} turns) · "
        f"subagents {fmt(sub_total)} ({len(subs)} spawns) · cache hit {cache_hit_pct(m):.0f}%"
    )


def report_agents(jsonl: Path):
    records = list(parse_records(jsonl))
    _, subs, _ = collect(records)
    if not subs:
        print("(no subagent dispatches in this session yet)")
        return
    total = sum(s["totalTokens"] for s in subs)
    print(f"## Subagent attribution — {len(subs)} spawns, {fmt(total)} tokens total")
    print()
    for i, s in enumerate(subs, 1):
        print(
            f"{i}. {s['agentType']}  {fmt(s['totalTokens'])} tok  "
            f"({fmt(s['input_tokens'])} in / {fmt(s['output_tokens'])} out / {fmt(s['cache_read_input_tokens'])} cache-r)  "
            f"{s['durationMs'] / 1000:.1f}s  {s['toolUseCount']} tools  {s['status']}"
        )
        print(f"   \"{s['prompt_preview']}…\"")


def save_mark(name: str, jsonl: Path):
    MARKS_DIR.mkdir(parents=True, exist_ok=True)
    size = jsonl.stat().st_size
    mark = {
        "name": name,
        "jsonl": str(jsonl),
        "byte_offset": size,
        "timestamp": time.time(),
        "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (MARKS_DIR / f"{name}.json").write_text(json.dumps(mark, indent=2))
    print(f"Marked '{name}' at byte {size} in {jsonl.name}")


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
    out = {
        "source": str(jsonl),
        "main_thread": {
            "turns": m["turns"],
            "tool_calls_issued": tool_calls,
            "input_tokens": m["input_tokens"],
            "output_tokens": m["output_tokens"],
            "cache_read_input_tokens": m["cache_read_input_tokens"],
            "cache_creation_input_tokens": m["cache_creation_input_tokens"],
            "total": main_thread_total(m),
            "cache_hit_pct": round(cache_hit_pct(m), 1),
        },
        "subagents": subs,
        "subagent_total": sum(s["totalTokens"] for s in subs),
        "grand_total": main_thread_total(m) + sum(s["totalTokens"] for s in subs),
    }
    print(json.dumps(out, indent=2))


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "summary"

    jsonl = current_session_jsonl()
    if not jsonl and mode not in ("marks", "drop"):
        slug = project_slug(Path.cwd())
        print(f"ERR: no JSONL found for project slug '{slug}'", file=sys.stderr)
        print(f"    expected dir: {PROJECTS_DIR / slug}", file=sys.stderr)
        sys.exit(1)

    if mode == "summary":
        report_summary(jsonl)
    elif mode == "quick":
        report_quick(jsonl)
    elif mode == "agents":
        report_agents(jsonl)
    elif mode == "raw":
        report_raw(jsonl)
    elif mode == "mark":
        if len(args) < 2:
            print("Usage: usage2 mark <name>", file=sys.stderr)
            sys.exit(1)
        save_mark(args[1], jsonl)
    elif mode == "since":
        if len(args) < 2:
            print("Usage: usage2 since <name>", file=sys.stderr)
            sys.exit(1)
        mk = load_mark(args[1])
        report_summary(Path(mk["jsonl"]), byte_offset=mk["byte_offset"],
                       label=f"since '{mk['name']}' ({mk['iso_time']})")
    elif mode == "marks":
        if not MARKS_DIR.exists():
            print("(no marks saved)")
            return
        for m in sorted(MARKS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
            d = json.loads(m.read_text())
            print(f"  {d['name']:<24}  {d['iso_time']}  ({Path(d['jsonl']).name[:13]}…  @ byte {d['byte_offset']})")
    elif mode == "drop":
        if len(args) < 2:
            print("Usage: usage2 drop <name>", file=sys.stderr)
            sys.exit(1)
        f = MARKS_DIR / f"{args[1]}.json"
        if f.exists():
            f.unlink()
            print(f"Dropped '{args[1]}'")
        else:
            print(f"No mark '{args[1]}'", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
