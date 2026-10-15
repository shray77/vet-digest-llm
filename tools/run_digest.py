#!/usr/bin/env python3
"""
tools/run_digest.py — прогон еженедельного дайджеста через llama.cpp + Qwen3-1.7B.

Вход (аргументы):
  --context-file PATH   JSON из build_context.py
  --llama PATH          бинарь llama-cli
  --model PATH          GGUF (Qwen3-1.7B-Q8_0)
  --out PATH            data/llm_digest.json
  --archive-dir PATH    data/archive

Модель пишет свободным текстом со структурой 1) / 2) / 3) — парсим строгим
разбором по маркерам, без JSON от LLM (малые модели не держат JSON-дискриплину).
Числа в JSON-выход берём ТОЛЬКО из stats контекста (не из текста модели).
"""
import argparse
import json
import pathlib
import re
import subprocess
import datetime

SYS = (
    "Ты — аналитик ветеринарной экосистемы VetHub. Тебе дают данные недели из живых источников "
    "(окна риска птичьего гриппа, мировые вспышки, отзывы препаратов РФ, свежие PubMed-статьи). "
    "Составь краткий еженедельный дайджест для практикующего ветеринарного врача строго в формате:\n"
    "1) 2-4 предложения — главные события недели строго по данным;\n"
    "2) до 5 буллетов через точку с запятой по доменам: эпизоотология; фармаконадзор; наука;\n"
    "3) 1-2 предложения — практический смысл для ветврача.\n"
    "Пиши ТОЛЬКО по данным ниже, не выдумывай числа и события. Если данных по домену нет — так и напиши. "
    "Русский язык, деловой тон, без эмодзи."
)


def parse_answer(text: str) -> dict:
    """Строгий парс структуры 1) 2) 3). Фолбэк — весь текст в summary."""
    t = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    t = re.sub(r"\[\s*Prompt:[\s\S]*$", "", t)          # хвост логов llama.cpp
    t = re.sub(r"Exiting\.\.\.\s*$", "", t).strip()      # финал интерактив-режима
    t = re.sub(r"^\s*assistant\s*", "", t).strip()
    m1 = re.search(r"1\)\s*([\s\S]*?)\n\s*2\)", t)
    m2 = re.search(r"2\)\s*([\s\S]*?)\n\s*3\)", t)
    m3 = re.search(r"3\)\s*([\s\S]*)$", t)
    def _clean(b: str) -> str:
        b = re.sub(r"\*\*", "", b)                            # markdown-жирный
        while re.match(r"^\s*[-—•*]\s*", b): b = re.sub(r"^\s*[-—•*]\s*", "", b)  # маркеры списков
        b = re.sub(r"^[Бб]уллеты по доменам\s*[:.]?\s*", "", b)  # заголовок-мусор
        b = re.sub(r"\s+", " ", b).strip()
        return b

    if m1 and m2 and m3:
        summary = re.sub(r"\s+", " ", m1.group(1)).strip()
        raw_bullets = m2.group(1).strip()
        # модель может дать либо ";", либо маркированные строки "- " — поддержим оба
        if raw_bullets.count("- ") >= 2 or "\n-" in raw_bullets or raw_bullets.startswith("-"):
            parts = [ln for ln in raw_bullets.splitlines() if ln.strip()]
        else:
            parts = raw_bullets.split(";")
        bullets = [_clean(b) for b in parts]
        bullets = [b for b in bullets if len(b) > 15][:6]
        practical = re.sub(r"\s+", " ", _clean(m3.group(1))).strip()
        practical = re.sub(r"^3\)\s*", "", practical)
    else:
        summary = re.sub(r"\s+", " ", t)[:1200]
        bullets, practical = [], ""
    return {"summary": summary, "bullets": bullets, "practical": practical}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--context-file", required=True)
    ap.add_argument("--llama", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--archive-dir", required=True)
    args = ap.parse_args()

    ctx = json.loads(pathlib.Path(args.context_file).read_text(encoding="utf-8"))
    prompt = (
        "<|im_start|>system\n" + SYS + "<|im_end|>\n"
        "<|im_start|>user\nДАННЫЕ НЕДЕЛИ:\n" + ctx["context"] +
        "\n\nСоставь дайджест. /no_think<|im_end|>\n<|im_start|>assistant\n"
    )

    cmd = [
        args.llama, "-m", args.model, "-p", prompt,
        "-n", "700", "--temp", "0.3", "--seed", "42",
        "-c", "4096", "-t", "4", "--no-display-prompt", "--single-turn", "--log-disable",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 and not r.stdout.strip():
        raise SystemExit(f"llama-cli failed: {r.stderr[-500:]}")
    parsed = parse_answer(r.stdout)
    if len(parsed["summary"]) < 80:
        raise SystemExit("digest слишком короткий — probable generation failure")

    week_start = ctx["week_start"]
    iso_year, iso_week, _ = datetime.date.fromisoformat(week_start).isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"

    out = {
        "service": "vet-digest-llm",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "week_key": week_key,
        "week_start": week_start,
        "week_end": ctx["week_end"],
        "model": {
            "name": "Qwen3-1.7B-Instruct (Q8_0)",
            "runtime": "llama.cpp",
            "license": "Apache-2.0",
            "note": "инференс на GitHub Actions runner, CPU; seed 42, temp 0.3",
        },
        "stats": ctx["stats"],
        "headline": (parsed["summary"][:110] + ("…" if len(parsed["summary"]) > 110 else "")),
        "summary": parsed["summary"],
        "bullets": parsed["bullets"],
        "practical": parsed["practical"],
        "sources": {
            "vetflu": "https://shray77.github.io/vetflu-site/",
            "biothreat": "https://shray77.github.io/biothreat-site/",
            "vetrecall": "https://shray77.github.io/vetrecall-site/",
            "vetdigest": "https://shray77.github.io/vetdigest-site/",
            "hub": "https://shray77.github.io/vet-hub/",
        },
        "disclaimer": (
            "Машинный дайджест: текст сгенерирован малой LLM (Qwen3-1.7B) по публичным данным "
            "экосистемы. Возможны ошибки и пропуски — проверяйте первоисточники. Не ветеринарный совет."
        ),
    }

    outp = pathlib.Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    adir = pathlib.Path(args.archive_dir)
    adir.mkdir(parents=True, exist_ok=True)
    (adir / f"{week_key}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # индекс архива (последние 12 недель)
    entries = []
    for f in sorted(adir.glob("*.json"), reverse=True)[:12]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            entries.append({"week_key": d.get("week_key"), "generated_at": d.get("generated_at"), "headline": d.get("headline")})
        except Exception:
            pass
    (adir / "list.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"OK: {week_key} → {outp} (bullets: {len(parsed['bullets'])}, summary {len(parsed['summary'])} зн.)")


if __name__ == "__main__":
    main()
