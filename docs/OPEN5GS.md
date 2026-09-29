# Подключение Open5GS и независимое наблюдение

Целевая версия шаблонов: Open5GS v2.7.6. Источники сверены 28.09.2026:

1. [SCP configuration](https://github.com/open5gs/open5gs/blob/v2.7.6/configs/open5gs/scp.yaml.in): sbi.server/client.nrf, адрес 127.0.0.200, порт 7777.
2. [NRF configuration](https://github.com/open5gs/open5gs/blob/v2.7.6/configs/open5gs/nrf.yaml.in): sbi.server, адрес 127.0.0.10, порт 7777.
3. [SCP indirect communication release](https://open5gs.org/open5gs/release/2022/11/18/release-v2.5.6.html): поддержка косвенного обмена через SCP.
4. [Quickstart](https://open5gs.org/open5gs/docs/guide/01-quickstart/): установка полного ядра при расширении эксперимента.

## Топология поставляемого live-режима

    Python workload → независимый HTTP/2 observer → Open5GS SCP → три Python AF
                                                      ↑
                                                  Open5GS NRF
    Python AF → NRF: регистрация / heartbeat / deregistration
    Verifier → observer → SCP → AF: КИЕ
    Collector → AF: отдельный самоотчёт закрытого окна

Все прикладные запросы действительно проходят через настоящий SCP. observer получает статус и время из собственного обмена, не читает внутренние счётчики AF. КИЕ также проходит через SCP. Регистрация AF выполняется стандартным PUT /nnrf-nfm/v1/nf-instances/{uuid}, обновление статуса — PATCH, удаление — DELETE. Значение heartBeatTimer берётся из ответа NRF. Проверка discovery выполняется непосредственно и через SCP.

Маршрутизация сервиса использует 3gpp-sbi-target-apiroot (явно заданный producer); это не демонстрация динамического выбора producer по NF Discovery. /lab/v1/read, update, kie, report — экспериментальные API, а не стандартизованные сервисы AMF/SMF. Профиль регистрирует AF, не выдаёт Python-объект за настоящую AMF. В Open5GS не добавляются неизвестные поля NFProfile.

Для h2c реализован клиент с HTTP/2 prior knowledge. Обычное включение http2=True у HTTPX недостаточно для такого обмена. На запрос выделяется отдельное соединение; это ограничивает применимость результатов задержки к промышленному пулу постоянных соединений.

## Docker — минимальный NRF/SCP стенд

Dockerfile собирает настоящий Open5GS из тега v2.7.6. Образ содержит NRF/SCP и Python; MongoDB, UE, gNB, UPF для этих SBI-проверок не нужны. Порты наружу не публикуются, всё выполняется внутри контейнера.

```bash
docker compose build
docker compose up -d --wait
docker compose exec core python3 -m nf_lab.cli preflight
docker compose exec core python3 -m nf_lab.cli live --scenario falsified-report --out runs/live-smoke
docker compose exec core python3 scripts/live_matrix.py runs/live-matrix
docker compose down
```

Полная матрица по умолчанию: 72 прогона × 24 окна × 5 секунд, около 2.5 часов плюс запуск/обработка. Для первого запуска используйте один прогон. `scripts/live_matrix.py` выполняется последовательно из-за фиксированных портов. Не используйте одну папку повторно. В `/opt/open5gs/SOURCE_COMMIT` сохраняется исходный commit; перенесите его в протокол эксперимента.

Матрица выполняется блоками по одному повтору каждого сценария; порядок внутри блока фиксированным seed случайно переставляется и записывается в `schedule.json`. Так изменение температуры, фоновой нагрузки или состояния системы во времени не совпадает систематически с одним сценарием.

Образ собран на компьютере подготовки проекта 29.09.2026. `preflight` прошёл с HTTP 200 от NRF и через SCP; один полный live-прогон `falsified-report` завершился успешно. Проверенный commit: `d9d3abdd480be96fac3bc8a997e83446648763ca`. Артефакты и краткая проверка лежат в `results/open5gs-smoke-20260929`. На другом компьютере повторите `preflight` и smoke-прогон до полной матрицы.

## Linux с уже установленным Open5GS

Запустите отдельные NRF/SCP с файлами `configs/open5gs/*.yaml`; не заменяйте конфиги действующей сети. В отдельном лабораторном namespace:

```bash
open5gs-nrfd -c "$PWD/configs/open5gs/nrf.yaml"
# В другом терминале:
open5gs-scpd -c "$PWD/configs/open5gs/scp.yaml"
# В активированной Python-среде:
python -m nf_lab.cli preflight
python -m nf_lab.cli live --scenario normal --out runs/live-normal-00
```

Если используются другие адреса, измените `configs/live.yaml`. На Windows запускайте live в Linux VM/WSL2 или Docker. Режим симуляции работает непосредственно на Windows.

## Штатные AMF/SMF/UDM/AUSF/PCF

SCP сам по себе не предоставляет счётчики, достаточные для всех признаков R/O. Проект не притворяется, что обычные Prometheus-метрики Open5GS содержат достоверный per-window журнал SBI. Для производственных NF требуются: конкретный счётчик запросов в самой NF/sidecar для R, независимый зеркальный захват для O, согласованные окна и отдельный контрольный агент КИЕ. Встроенный live-стенд даёт воспроизводимый эксперимент именно за счёт управляемых AF. Патчи C-кода настоящих AMF/SMF здесь не поставляются.

Пассивный импорт уже реализован и применим к стандартным SBI API:

```bash
# Начать до открытия HTTP/2 соединений; в отдельном терминале, на Linux:
sudo tshark -i lo -f 'tcp port 7777' -w runs/sbi.pcapng
# После завершения захвата:
python -m nf_lab.cli pcap --input runs/sbi.pcapng --map configs/nf-ip-map.json --out runs/passive-01
```

Импорт сопоставляет пары по (tcp.stream, http2.streamid), берёт только плечо SCP→producer, не считает оба плеча дважды. NF определяется статическим IP map, а не присланным NF заголовком. `latency_ms` здесь — время до заголовков ответа; у proxy observer — до полного ответа, эти ряды нельзя смешивать. Несколько NF на одном IP необходимо развести по IP или расширить mapping до адреса+порта. Пример map подходит типовой loopback конфигурации Open5GS, не трём AF на одном IP.

`capture_quality.json` показывает неотвеченные запросы и orphan responses. Отсутствие таких ошибок ещё не доказывает полный захват. TLS без ключей не раскрывает SBI; параметр `--keylog` позволяет передать tshark ключи лабораторного сеанса, но TLS режим не включён в проверенный профиль проекта. При захвате после начала соединения HPACK может не восстановиться. Файл `observations.jsonl` — независимое свидетельство, а не автоматически размеченный датасет; не присваивайте ему метки из синтетического генератора.

В основной профиль намеренно не включены TLS/OAuth и утверждение о действительных полномочиях скомпрометированной NF. Это отдельный уровень экспериментальной проверки модели угроз, необходимый до переноса выводов на защищённый SBA.
