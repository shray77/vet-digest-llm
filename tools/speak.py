#!/usr/bin/env python3
"""
tools/speak.py — аудио-версия дайджеста: data/llm_digest.json → data/digest_latest.mp3.

Движок: piper (CPU, onnxruntime) + голос ru_RU-dmitri-medium.
Бинарь/голос качает weekly.yml (кэшируются actions/cache), сюда приходят путями.

Нормализация текста под русский TTS (без неё звук мусорный):
  - аббревиатуры флайвеев → полные названия (номинатив — грамматика LLM-текста сохраняется);
  - «R-индекс 100–85» → «Р-индекс от 100 до 85»; LLM → «языковая модель»;
  - вырезается (Qwen3-1.7B); даты/номер недели — прописью.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import wave
import contextlib

UNITS_F = ["первая", "вторая", "третья", "четвертая", "пятая", "шестая",
           "седьмая", "восьмая", "девятая"]
TEEN_F = ["десятая", "одиннадцатая", "двенадцатая", "тринадцатая", "четырнадцатая",
          "пятнадцатая", "шестнадцатая", "семнадцатая", "восемнадцатая", "девятнадцатая"]
TENS_NOM = {2: "двадцать", 3: "тридцать", 4: "сорок", 5: "пятьдесят"}
TENS_F = {2: "двадцатая", 3: "тридцатая", 4: "сороковая", 5: "пятьдесятая"}
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
YEARS_GEN = {2024: "две тысячи двадцать четвертого", 2025: "две тысячи двадцать пятого",
             2026: "две тысячи двадцать шестого", 2027: "две тысячи двадцать седьмого",
             2028: "две тысячи двадцать восьмого", 2029: "две тысячи двадцать девятого",
             2030: "две тысячи тридцатого"}

FLYWays = {"В-Атл": "Восточно-Атлантический", "Черном": "Черноморский",
           "Центр-Аз": "Центрально-Азиатский", "В-Азия": "Восточно-Азиатский"}


def week_num_fem(n: int) -> str:
    if 1 <= n <= 9:
        return UNITS_F[n - 1]
    if 10 <= n <= 19:
        return TEEN_F[n - 10]
    t, u = divmod(n, 10)
    return TENS_F[t] if u == 0 else f"{TENS_NOM[t]} {UNITS_F[u - 1]}"


def day_ord_neut(d: int) -> str:
    """Средний род (для «по …»): пятое, десятое, двадцатое."""
    if 1 <= d <= 9:
        return UNITS_F[d - 1].replace("ая", "ое")
    if 10 <= d <= 19:
        return TEEN_F[d - 10].replace("ая", "ое")
    if d == 20 or d == 30:
        return "двадцатое" if d == 20 else "тридцатое"
    if d == 31:
        return "тридцать первое"
    t, u = divmod(d, 10)
    return f"{TENS_NOM[t]} {UNITS_F[u - 1].replace('ая', 'ое')}"


def day_ord_gen(d: int) -> str:
    """Родительный (для «с …»): пятого, десятого, двадцатого."""
    return day_ord_neut(d).replace("ое", "ого")


def tts_text(d: dict) -> str:
    wk = d.get("week_key") or ""
    try:
        y, wnum = wk.split("-W")
        wnum = int(wnum)
    except Exception:
        y, wnum = "", 0
    try:
        d1 = d.get("week_start") or ""
        d2 = d.get("week_end") or ""
        m1 = MONTHS_GEN[int(d1[5:7]) - 1]
        m2 = MONTHS_GEN[int(d2[5:7]) - 1]
        span = (f"с {day_ord_gen(int(d1[8:10]))} {m1}"
                f" по {day_ord_neut(int(d2[8:10]))} {m2}" if d1 and d2 else "")
    except Exception:
        span = ""
    intro = "Ветеринарный дайджест"
    if wnum:
        yr = f", {YEARS_GEN.get(int(y), y)} года" if y else ""
        intro = f"Ветеринарный дайджест, неделя {week_num_fem(wnum)}{yr}"
        if span:
            intro += f", {span}"
    intro += "."

    s = d.get("summary") or ""
    for abbr, full in FLYWays.items():
        s = re.sub(rf"\b{re.escape(abbr)}\b", full, s)
    s = re.sub(r"R-индекс\s*(\d+)\s*[–-]\s*(\d+)", r"Р-индекс от \1 до \2", s)
    s = s.replace("R-индекс", "Р-индекс")

    bullets = " ".join(d.get("bullets") or [])
    bullets = re.sub(r"\*\*", "", bullets)
    bullets = re.sub(r"\s+-\s+", ". ", bullets).strip()

    practical = (d.get("practical") or "").strip()
    disc = re.sub(r"\s*\(Qwen3[\d..kmKMB-]*\)", "", d.get("disclaimer") or "")
    disc = disc.replace("малой LLM", "малой языковой моделью")
    disc = disc.replace("LLM", "языковой модели")

    parts = [intro, s, bullets, practical, disc]
    return re.sub(r"\s+", " ", " ".join(p.strip() for p in parts if p.strip()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", default="data/llm_digest.json")
    ap.add_argument("--piper", default="runner-piper/piper")
    ap.add_argument("--voice", default="voice/ru.onnx")
    ap.add_argument("--out", default="data/digest_latest")
    args = ap.parse_args()

    d = json.load(open(args.digest, encoding="utf-8"))
    text = tts_text(d)
    print(f"текст: {len(text)} симв.")
    print(text[:220], "...")

    wav = f"/tmp/{os.path.basename(args.out)}.wav"
    env = dict(os.environ,
               LD_LIBRARY_PATH=f"{os.path.dirname(os.path.abspath(args.piper))}:"
                               f"{os.environ.get('LD_LIBRARY_PATH', '')}")
    proc = subprocess.run(
        [args.piper, "--model", args.voice, "--output_file", wav,
         "--sentence_silence", "0.25"],
        input=text.encode("utf-8"), env=env, capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"piper failed: {proc.stderr.decode()[:800]}")
    with contextlib.closing(wave.open(wav)) as w:
        dur = w.getnframes() / w.getframerate()
    print(f"WAV: {dur:.1f} с")

    mp3 = args.out + ".mp3"
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                        "-codec:a", "libmp3lame", "-q:a", "5", "-ar", "22050",
                        "-ac", "1", mp3], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("WARN: ffmpeg недоступен — кладём wav")
        mp3 = args.out + ".wav"
        subprocess.run(["cp", wav, mp3], check=True)
    print(f"OUT: {mp3} ({os.path.getsize(mp3) / 1e3:.0f} кБ, {dur:.1f} с)")


if __name__ == "__main__":
    main()
