# NF Veracity Lab — экспериментальный стенд Python / Open5GS

Готовый исследовательский проект для сравнения M0–M4 при фальсификации самоотчётности NF. Два режима: воспроизводимый синтетический эксперимент и управляемые Python AF, использующие реальные NRF/SCP Open5GS. Дополнительно — импорт независимого захвата штатного SBI.

**Не смешивайте результаты режимов:** синтетические данные не являются измерениями Open5GS. На машине подготовки собран Open5GS v2.7.6 и выполнен один реальный сценарий через NRF/SCP с управляемыми Python AF. Его данные лежат в `results/open5gs-smoke-20260929`. Полная 72-прогонная матрица на Open5GS ещё не выполнена.

## Быстрый запуск без Open5GS

Python 3.11+; контейнер использует Python из Ubuntu 24.04 в отдельной виртуальной среде.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python -m nf_lab.cli simulate --out runs/synthetic-01
```

Windows PowerShell: вместо `source` используйте `.venv\Scripts\Activate.ps1`. Все команды выполняются из каталога проекта. Уже выполненный синтетический прогон находится в `results/synthetic-seed731`; повторный запуск требует новой папки.

## Что включено

```text
nf_lab/
  cli.py             команды simulate / live / preflight / evaluate / pcap
  simulate.py        шесть воспроизводимых сценариев
  live.py            экспериментальные AF, наблюдатель, NRF, КИЕ, сбор окон
  h2client.py        HTTP/2 prior-knowledge транспорт
  pcap.py            пассивный импорт tshark JSON
  features.py        D_rep, D_peer, временной контекст, M0–M4
  evaluate.py        обучение, метрики, абляция, bootstrap
  io.py              экспорт и протокол окружения
configs/             параметры эксперимента и Open5GS NRF/SCP
scripts/             запуск ядра и полной live-матрицы
tests/               проверки методики и сетевого тракта Python
docs/METHOD.md       формулы, метки, предпосылки, ограничения
docs/OPEN5GS.md      запуск и подключение к настоящему Open5GS
results/            выполненный синтетический эксперимент
Dockerfile
compose.yaml
requirements.txt
requirements-tested.txt
pyproject.toml
```

В `results/` также сохранён [контрольный прогон Open5GS](results/open5gs-smoke-20260929/verification.json) с первичными событиями и окнами. Он подтверждает работоспособность сетевого тракта, но одного прогона недостаточно для оценки F1/AUC и выводов статьи.

## Модели и результаты

M0: SBI → M1: + время → M2: + КИЕ → M3: + D_peer → M4: + D_rep/отсутствие отчёта. Один классификатор, одинаковые прогоны, порог только по validation. Две отдельные задачи: обнаружение атаки и обнаружение фальсификации. `load-spike` — отрицательный контроль.

| Файл результата | Содержание |
|---|---|
| windows.jsonl | оконные измерения и отдельные метки сценариев |
| events.jsonl | первичные live-события observer/report/KIE/NRF |
| dataset.csv | признаки, исходные измерения и метаданные |
| metrics.csv | F1, Recall, FPR, AUC и другие метрики по сценарию |
| predictions.csv | оценки и решения на тестовых прогонах |
| ablation.csv | разница F1 и 95% CI, включая M3→M4 |
| curves.csv | точки ROC/PR при разных порогах |
| timings.csv | время обучения и применения моделей |
| split.json / scales.json | разбиение и нормировка, фиксируемые до теста |
| manifest.json | конфиг, версии библиотек, хеши кода |
| summary.json | сводка и оценка прикладного трафика КИЕ |
| completion.json | признак успешного завершения прогона |

Для повторной оценки нескольких независимых live-прогонов:

```bash
python -m nf_lab.cli evaluate --inputs runs/run01/windows.jsonl runs/run02/windows.jsonl --out runs/evaluation
```

Это пример синтаксиса: для оценки требуется минимум пять независимых прогонов **каждого** включённого сценария, нормальные обучающие данные и обе метки. Полную матрицу строит `python scripts/live_matrix.py runs/live-matrix` внутри запущенного стенда.

Параметры меняются в YAML. Не подбирайте их по тестовым результатам. Для диссертации сначала зафиксируйте протокол, сохраните версии Open5GS и конфиги, затем соберите независимые реальные прогоны. Подробные определения D_rep/D_peer, границы новизны и методики приведены в [METHOD.md](docs/METHOD.md); команды настоящего NRF/SCP — в [OPEN5GS.md](docs/OPEN5GS.md).

Пошаговый протокол для статьи и критерии перехода от экспериментальных AF к штатным AMF/SMF приведены в [ARTICLE_RUN.md](docs/ARTICLE_RUN.md).
