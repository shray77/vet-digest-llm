#!/usr/bin/env python3
"""
tools/build_context.py — сбор контекста недели из живых JSON экосистемы vet-*.

Источники (публичные, без ключей):
  vetflu-site   model.json    — R-индекс ВПГП по флайвеям (узлы: r/chan)
  biothreat     events.json   — мировые вспышки (канон болезней/стран)
  vetrecall     signals.json  — сигналы фармаконадзора РФ
  vetdigest     digest.json   — PubMed-тезисы недели
  rss-core      status.json   — здоровье источников

Выход: STDOUT — JSON {week_start, week_end, generated_at, stats, context},
context — компактный текстовый контекст для LLM (RU). Любой источник,
упавший/протухший (>30 ч), помечается в контексте как недоступный —
LLM честно увидит дыру в данных.
"""
import json
import re
import urllib.request
import datetime
import sys
import os
import pathlib

RAW = {
    "flu": "https://raw.githubusercontent.com/shray77/vetflu-site/main/data/model.json",
    "events": "https://raw.githubusercontent.com/shray77/biothreat-site/main/events.json",
    "signals": "https://raw.githubusercontent.com/shray77/vetrecall-site/main/signals.json",
    "digest": "https://raw.githubusercontent.com/shray77/vetdigest-site/main/digest.json",
    "status": "https://raw.githubusercontent.com/shray77/rss-core-site/main/status.json",
    "tspulse": "https://raw.githubusercontent.com/shray77/vet-pilots/main/tspulse/latest.json",
}
FRESH_H = 30          # протухание источника
N_ITEMS = 12          # записей на домен
CACHE_DIR = "/tmp/vdl_cache"


def fetch(name: str, url: str):
    """Скачивает JSON; кэш на диск, чтобы ретраи не били источники."""
    pathlib.Path(CACHE_DIR).mkdir(exist_ok=True)
    cache = pathlib.Path(CACHE_DIR) / f"{name}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 vet-digest-llm"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        cache.write_bytes(data)
        return json.loads(data)
    except Exception as e:
        if cache.exists():                       # протухший кэш лучше пустоты
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                pass
        print(f"WARN {name}: {e}", file=sys.stderr)
        return None



def clean_title(t: str) -> str:
    """HTML-энтити + схлопывание пробелов."""
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(t or "")).strip()


def fresh_unique(items, key_fields, n):
    """Свежайшие N записей без дублей по ключу и без '?'-мусора."""
    seen, out = set(), []
    for x in items:
        vals = [clean_title(str(x.get(k) or "")) for k in key_fields]
        if any(v in ("?", "") for v in vals):
            continue
        key = "|".join(v.lower() for v in vals)
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
        if len(out) >= n:
            break
    return out


def age_h(iso: str) -> float:
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return 1e9


def main() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    week_end = now.date()
    week_start = week_end - datetime.timedelta(days=6)
    lines = [f"Неделя: {week_start.isoformat()} .. {week_end.isoformat()} (UTC)"]
    stats = {}

    # --- VETFLU: R-индекс по флайвеям (максимум по узлам) ---
    flu = fetch("flu", RAW["flu"])
    if flu and age_h(flu.get("generated_at", "")) <= FRESH_H:
        lines.append("=== VETFLU: окна риска ВПГП (R-индекс 0-100, максимум по узлам флайвея) ===")
        n = 0
        for fw in flu.get("flyways") or []:
            nodes = (flu.get("nodes") or {}).get(fw.get("id")) or []
            if not nodes:
                continue
            top = max(nodes, key=lambda x: x.get("r") or 0)
            lines.append(f"  {fw.get('short') or fw.get('label')}: R={top.get('r')} "
                         f"(узел {top.get('name')}, канал {top.get('chan')})")
            n += 1
        al = flu.get("alert") or {}
        if al.get("level"):
            lines.append(f"  Общий уровень экосистемы: {al.get('level')} (max R={al.get('max_r')})")
        stats["flu_flyways"] = n
    else:
        lines.append("=== VETFLU: данные недоступны/протухли ===")
        stats["flu_flyways"] = 0

    # --- БИОУГРОЗЫ ---
    ev = fetch("events", RAW["events"])
    if ev and age_h(ev.get("generated_at", "")) <= FRESH_H:
        items = fresh_unique(sorted(ev.get("items") or [], key=lambda x: x.get("published_at") or "", reverse=True), ["disease", "country"], N_ITEMS)
        lines.append(f"=== БИОУГРОЗЫ: мировые вспышки ({len(items)} свежайших, без дублей) ===")
        for e in items:
            dc = clean_title(e.get("disease") or "?")
            cn = clean_title(e.get("country") or "?")
            lv = e.get("level") or ""
            dt = (e.get("published_at") or "")[:10]
            dup = f", дублей: {e['dup_count']}" if e.get("dup_count") else ""
            lines.append(f"  {dc} — {cn}, уровень: {lv}, {dt}{dup}")
        stats["events"] = len(items)
    else:
        lines.append("=== БИОУГРОЗЫ: данные недоступны/протухли ===")
        stats["events"] = 0

    # --- АНОМАЛИЯ НЕДЕЛИ (TSPulse, vet-pilots; источник недельный — окно свежести шире) ---
    tsp = fetch("tspulse", RAW["tspulse"])
    if tsp and age_h(tsp.get("generated_at", "")) <= 90:
        wk = tsp.get("week") or {}
        lines.append("=== АНОМАЛИЯ НЕДЕЛИ (TSPulse по суточным вспышкам РФ, score 0-1, порог 0.6) ===")
        lines.append(f"  вердикт: {wk.get('verdict')}; max score {wk.get('max_score')} "
                     f"({wk.get('percentile_max')} перцентиль истории); "
                     f"дней выше порога: {wk.get('days_over_threshold')}")
        wd = tsp.get("week_diseases") or {}
        if wd:
            lines.append("  болезни недели: " + "; ".join(f"{k} ({v})" for k, v in list(wd.items())[:4]))
        stats["anomaly_verdict"] = wk.get("verdict") or "?"
        stats["anomaly_percentile"] = wk.get("percentile_max")
    else:
        lines.append("=== АНОМАЛИЯ НЕДЕЛИ: данные TSPulse недоступны/протухли ===")
        stats["anomaly_verdict"] = "нет данных"

    # --- ФАРМАКОНАДЗОР ---
    sg = fetch("signals", RAW["signals"])
    if sg and age_h(sg.get("generated_at", "")) <= FRESH_H:
        items = fresh_unique(sorted(sg.get("items") or [], key=lambda x: x.get("published_at") or "", reverse=True), ["title"], N_ITEMS)
        lines.append(f"=== ОТЗЫВЫ/СИГНАЛЫ препаратов РФ ({len(items)} свежайших, без дублей) ===")
        for s in items:
            t = clean_title(s.get("title") or s.get("product") or "?")[:140]
            lines.append(f"  [{s.get('signal_type') or '?'}] {t} ({(s.get('published_at') or '')[:10]})")
        stats["signals"] = len(items)
    else:
        lines.append("=== ОТЗЫВЫ/СИГНАЛЫ: данные недоступны/протухли ===")
        stats["signals"] = 0

    # --- PUBMED ---
    dg = fetch("digest", RAW["digest"])
    if dg and age_h(dg.get("generated_at", "")) <= FRESH_H:
        items = fresh_unique(sorted(dg.get("items") or [], key=lambda x: (x.get("digest_date") or "", x.get("impact") or 0), reverse=True), ["title"], N_ITEMS)
        lines.append(f"=== PUBMED-ТЕЗИСЫ ({len(items)} свежих, с impact, без дублей) ===")
        for i in items:
            lines.append(f"  [{i.get('digest_date')}] ({i.get('impact')}★) {clean_title(i.get('title') or '?')[:140]}")
        stats["pubmed"] = len(items)
    else:
        lines.append("=== PUBMED-ТЕЗИСЫ: данные недоступны/протухли ===")
        stats["pubmed"] = 0

    # --- ЗДОРОВЬЕ ИСТОЧНИКОВ ---
    st = fetch("status", RAW["status"])
    if st:
        srcs = st.get("sources") or []
        ok = sum(1 for s in srcs if s.get("last_status") == "ok" or (s.get("last_status") != "err" and s.get("consecutive_failures", 0) == 0))
        lines.append(f"=== ИНФРАСТРУКТУРА: источников ok {ok}/{len(srcs)} ===")
        stats["sources_ok"] = f"{ok}/{len(srcs)}"
    else:
        lines.append("=== ИНФРАСТРУКТУРА: статус недоступен ===")

    out = {"week_start": week_start.isoformat(), "week_end": week_end.isoformat(),
           "generated_at": now.isoformat(timespec="seconds"), "stats": stats,
           "context": "\n".join(lines)}
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
