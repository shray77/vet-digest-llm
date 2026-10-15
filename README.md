# VetDigest LLM

Еженедельный **машинный дайджест** ветеринарной экосистемы [shray77](https://github.com/shray77): языковая модель собирает сводку недели из живых JSON-каналов экосистемы прямо на GitHub Actions-раннере — без API-ключей, без GPU, без внешних сервисов.

**Живая витрина:** https://shray77.github.io/vet-digest-llm/

## Как это работает

```
пн 06:00 UTC (GitHub Actions, public runner — бесплатно)
  1. build_context.py — собирает неделю из живых JSON (vetflu R-индекс,
     мировые вспышки, сигналы РФ, PubMed-тезисы, здоровье источников);
     протухшие (>30 ч) источники честно помечаются в контексте
  2. run_digest.py — llama.cpp (CPU) + Qwen3-1.7B-Instruct Q8_0 (Apache-2.0):
     prompt со структурой 1)/2)/3), /no_think, seed 42, temp 0.3;
     строгий парс ответа (без JSON от LLM)
  3. data/llm_digest.json + архив data/archive/YYYY-Wnn.json + list.json
  4. коммит бот-ом → Pages
```

Кэш: модель (~1.8 ГБ) и рантайм llama.cpp (пин `b11645`) живут в `actions/cache` —
паттерн [vetflu-forecast](https://github.com/shray77/vetflu-forecast).

## Почему так

- **Не монолит**: агрегатор поверх живых каналов, ни один тул экосистемы не тронут (урок ВетХаба, Task 62).
- **Числа — из данных, текст — из модели**: JSON-выход наполняется `stats` напрямую из источников; модель отвечает только за текст. Малые модели не держат JSON-дисциплину — поэтому свободный текст со строгим парсером.
- **Qwen3-1.7B Q8_0** (официальный GGUF Qwen, Apache-2.0): русский лучше Qwen2.5-1.5B, ~80 с генерации на 4-ядерном раннере. TimesFM-уроки (Task 56/65) здесь не применимы — это не прогноз, а суммаризация.
- **Деградация честная**: упавший источник виден модели как «данные недоступны» — дайджест не молчит.

## Дисклеймер

Машинный дайджест возможен с ошибками: **проверяйте первоисточники**. Не ветеринарный совет — консультируйтесь с ветеринарным врачом, даже если вы сами ветеринарный врач. [Условия и дисклеймер](https://shray77.github.io/vet-digest-llm/terms.html).

## Структура

```
tools/build_context.py   # контекст недели из живых JSON (RU)
tools/run_digest.py      # llama.cpp инференс + строгий парс → JSON
.github/workflows/       # weekly.yml (cron+dispatch), pages.yml
data/llm_digest.json     # свежий выпуск
data/archive/            # YYYY-Wnn.json + list.json (12 недель)
index.html, terms.html   # витрина (тёмная, hub-консистентная)
```

## Хаб

Карточка дайджеста — на [ВетХабе](https://shray77.github.io/vet-hub/) и [визитке](https://shray77.github.io/).
