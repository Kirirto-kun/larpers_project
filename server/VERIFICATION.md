# Control plane: результаты проверки 2026-11-14

Область: часть 3 плана, ветка `codex/control-plane`. Проверки выполнены на одном физическом Mac с Docker, настоящим PostgreSQL 17.12 и локальным HTTPS с доверенным CA. Два зарегистрированных тестовых узла в API-тестах не считаются двумя физическими устройствами.

## Подтверждённые результаты

| Проверка | Результат |
|---|---|
| Серверные проверки | 51 pytest tests на отдельной схеме PostgreSQL; auth/CSRF, конкурентный claim, fencing, upload, completion, planner, recovery, benchmark, probe и search |
| Контракты | OpenAPI, 11 JSON Schema и fixtures проходят проверку ссылок, схем и отсутствия drift |
| Генерация клиентов | TypeScript strict compile, Go DTO compile и Python compile |
| Статический анализ | Ruff проходит для server/campus, server/tests, contracts, infra |
| Миграции | Чистая БД в тестах проходит 0001 → 0002 → 0003; существующая БД обновлена; `alembic check` не находит расхождений |
| Реальный AI | DeepSeek `/models` и JSON response проверены; `deepseek-flash` сформировал готовый план по явному заданию и зарегистрированному каталогу |
| HTTPS → AI → Docker → download | Job `3e88a567-9f81-4c64-92ee-c71d226c2b8d`, node `3f3c86ad-8b09-402e-a666-8a82e726fdc0`; full validation, 1 accepted unit, повтор complete возвращает ту же квитанцию |
| Контроль результата | Новый вход с Unicode; SHA-256 `6ff6598b504c2550b40be1c0fd707f56e6b4b31123d8aa68db154017797acd59`; значение вычислено в контейнере без сети, сверено независимым серверным валидатором и после скачивания |
| Restart | После остановки и запуска API принятый job и скачивание результата сохранились |
| Backup/restore | Снимок `2026-11-14T10:16:48.113528+00:00` восстановлен в отдельный Compose project/volumes; 6 referenced blobs, 0 missing; для AI job остался один acceptance и совпали checksum файлов |
| Portal/Console | Production build обеих apps из `origin/codex/portal-console` прошёл; `/`, `/jobs/test`, `/admin/`, `/admin/nodes`, JS/CSS assets и API health доступны с одного доверенного HTTPS origin |
| Report tools | В изолированной копии UI-ветки: 17 pytest tests и 3 subtests прошли |

Live smoke использует небольшой checksum workload с точным шаблоном команды в AI-запросе. Он подтверждает подключение провайдера и серверный путь выполнения. Он не измеряет качество планирования произвольных проектов, semantic search по настоящему корпусу или обучение ML.

## Разбор остальных серверных шагов

| Область | Реализация и проверка |
|---|---|
| Auth и изоляция | Приглашения, scoped bearer, browser cookie/CSRF, отзыв credentials; cross-pool запрет проверен для previews, jobs, logs, events, artifacts |
| Хранилище состояния | PostgreSQL migrations, уникальные task acceptance/ledger, атомарная выдача; idempotency body conflict и потеря ACK покрыты тестами |
| Бюджет после отмены | Отдельное `reserved_until` предотвращает выдачу того же бюджета, пока старый worker ещё вправе исполняться; cancel/reaper/session fencing не освобождают его преждевременно |
| Планировщик AI | Данные выбранных файлов не исполняются; проверяются права, sandbox paths, DAG, digest, variant, CUDA и resume; stale answers/claims не меняют новую ревизию |
| Ошибки провайдера | Максимум 3 обращения в deadline; 429, 5xx, timeout, refusal, incomplete, invalid schema; key и содержимое проекта отсутствуют в audit; существующие задачи работают при отказе AI |
| Probe | Зарегистрированный stage, отдельный deadline и accepted validation; максимум один неархивный sample до 64 KiB, JSON output до 16 KiB/файл; evidence получает только текущая preview; нет credits; cancel отзывает probe |
| Leases/results | DB clock, generation/token/session, expiry до reaper, отдельный durable verification claim, поздний validator и повтор complete не создают двойного acceptance |
| Uploads | SHA/размер частей, resume, atomic publish/fsync до DB reference, повтор finalize; моделируется падение между публикацией и commit |
| Checkpoint | ZIP с полным manifest, compatibility digest, checksum каждого файла и обязательными файлами адаптера; незавершённый checkpoint не становится ready; replay старой sequence не откатывает pointer |
| Scheduler | Сначала ресурсы/runtime, затем owner fairness; среди свежих задач одного владельца учитываются cache hints, скорость передачи и принятая скорость вычислений, после 30 секунд ожидания сохраняется FIFO; небольшие адаптивные батчи только до первого lease; accepted durations влияют на размер; OOM сохраняет item IDs и имеет конечный retry budget; unsupported resume не повторяется |
| Recovery | Drain разрешает renew, Stop блокирует; expiry/restart/validator claim recovery; DAG ждёт accepted predecessors; cancellation сохраняет ранее принятые единицы |
| Search | Отдельный job из принятого индекса; проверяются index/model digests и top_k; search не добавляет units и ledger к построению индекса |
| Benchmark | Standalone → single-node service → pool; одинаковый concurrency на каждом узле; уникальные accepted units и фактические участники; несовпадающие серии исключаются; worker timings помечены как сообщённые исполнителем |
| Размещение | Публичный HTTPS origin, volumes, CA export, SPA routes, восстановление на независимых volumes; scripts явно читают корневой `.env` |

## Передача интегратору и коллеге

Пользователь подтвердил, что Go runner и проверки на физических CPU/CUDA-устройствах выполнит коллега. Эти пункты переданы ему и не объявлены результатами серверной проверки. Для интеграции подготовлен [RUNNER_HANDOFF.md](RUNNER_HANDOFF.md).

Что требуется для приёмки всего проекта:

1. **Совместимость браузерных клиентов.** UI-ветка остаётся самостоятельной, как указано в её `docs/submission/integration-handoff.md`. Production serving проверен; авторизованный пользовательский маршрут не пройден. Финальному интегратору нужно подключить generated DTO и заменить provisional модели. Например, login принимает `token`; upload creation требует полный SHA-256; статус возвращает `parts[]`; finalize возвращает `Artifact.id`; preview использует `plan_revision`, `plan` и одну `question`. Нельзя выдавать успешную сборку за совместимость API.
2. **Go runner и workload adapters.** В доступных remote refs нет `codex/runner-cli`, в рабочей ветке отсутствует исполняемый runner. Smoke использует небольшой Python HTTP harness. Нужны реальный runner/watchdog, CPU/CUDA окружения и адаптеры embeddings/training. Поддержка checkpoint manifest на сервере не доказывает восстановление optimizer/RNG/dataloader в training-коде.
3. **Физические устройства и кампусная сеть.** Нет адресов разрешённых дополнительных вычислителей/CUDA-узла. Требуются inventory, проверка фактического CUDA, TLS/upload/download/reconnect с целевого SSID, >=2 физических участника с полезными acceptance, отключение CPU-worker и возвращение GPU с checkpoint.
4. **Измерения.** Выполнить минимум три сопоставимых standalone/service/pool повтора на новых данных с достаточным объёмом работы. Сохранить raw observations и итоговый интеграционный SHA. Scheduler использует optional cache/transfer hints устройства и accepted durations; на физических workload необходимо проверить точность этой оценки, а образы подготовить заранее.

Ключ DeepSeek хранится в игнорируемом `.env` с ограниченными правами и не включён в отчёт, fixtures, коммиты или логи. Временные CA/CLI credentials и backup находятся вне репозитория. Снимок выполнен на этом же физическом компьютере: это проверка логического восстановления, а не доказательство сохранности после потери самого ноутбука. Для эксплуатации нужна копия на отдельном устройстве.

## Воспроизведение

Команды настройки, генерации, тестов, AI smoke и backup/restore находятся в [README](../README.md). Для локальной тестовой БД на нестандартном порту:

```sh
TEST_DATABASE_URL=postgresql+psycopg://campus:campus@127.0.0.1:55433/campus uv run --project server pytest server/tests -q
uv run --project server python contracts/check.py
uv run --project server ruff check server/campus server/tests contracts infra
python3 infra/smoke.py --ai --origin https://localhost:58443 --ca /path/to/local-ca.pem --credential-file /path/to/credential.json
```

Значения в последней команде относятся к своей тестовой установке; секретные файлы не должны добавляться в Git. Live AI требует доступного настроенного провайдера. Физические проверки выполняются после получения runner и разрешённых устройств.
