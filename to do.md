# Portal + Console — to do

Рабочая ветка: `codex/portal-console`. Здесь выполняем обе лёгкие части: `codex/portal` и `codex/console` из общего плана.

Источник задач: [план реализации в main](https://github.com/Kirirto-kun/larpers_project/blob/main/docs/superpowers/plans/2026-11-14-campus-pool-implementation.md), части 1–2, интеграция и методика измерений. Дополнение: [системный дизайн в main](https://github.com/Kirirto-kun/larpers_project/blob/main/docs/superpowers/specs/2026-11-14-campus-pool-design.md).

Проверено 2026-11-14: `main` на коммите `f1914dddd8324cf8a63506fb6e7085b567f08356`. Перед новым этапом сверяем изменения плана в `main`, оставаясь в своей ветке.

## Текущий статус реализации

Самостоятельные portal и console реализованы. `main` проверен через `git fetch origin main` без переключения ветки. По указанию пользователя финальная совместимость, интеграция API и merge переданы отдельному агенту: отсутствие foundation не блокирует локальную разработку. До его появления используются явно обозначенные provisional UI models и synthetic fixtures. Ссылки на main выше остаются источником требований.

Локальные доказательства: обе production-сборки и чистые установки npm ci прошли; 17 Python-тестов, 9 console и 10 portal Playwright-сценариев PASS. Native download проверяет содержимое файла. Desktop portal/console/benchmark и mobile NewJob просмотрены: runtime errors отсутствуют, горизонтального overflow на мобильном нет. Оба npm audit: 0 advisories.

Пользователь подтвердил 2026-11-14: реального API, inventory и benchmark-данных нет — «ничего нету оставь». Поэтому unchecked пункты ниже о foundation/интеграции, физических измерениях, inventory, людях и финальном merged SHA **отложены по указанию пользователя**, а не выданы за выполненные. Финальная совместимость и merge переданы другому агенту. Все самостоятельные программные задачи выполнены; полное локальное ревью: [review.md](docs/submission/review.md), пакет передачи: [integration-handoff.md](docs/submission/integration-handoff.md).

## 0. Подготовка и границы

- [x] Создать и переключиться на `codex/portal-console`, подключить одноимённую ветку GitHub.
- [x] Прочитать задачи частей 1–2 из `main`.
- [ ] Получить foundation от control-plane: OpenAPI, схемы, fixtures, mock API и генератор DTO. На проверенном `main` их пока нет.
- [ ] Подключить согласованный foundation в нашу ветку перед интеграцией API; не придумывать собственные несовместимые DTO.
- [x] Создать два отдельных React + TypeScript + Vite приложения с Tailwind/shadcn, собственными package.json, lockfile, router и сборкой.
- [ ] Настроить генерацию DTO перед build/test; generated-файлы не редактировать и не коммитить.
- [x] Соблюдать пути владения: `apps/portal/**`, `apps/console/**`, `tools/benchmark/**`, `docs/submission/**`. Этот список задач добавлен в корень по запросу пользователя.
- [x] Делать English UI, поддержать Unicode и примеры EN/ZH; все UI-ассеты хранить локально, без CDN.

## 1. Пользовательский портал — apps/portal

Результат: человек может подключить ноутбук, запустить задачу и получить файлы без помощи разработчика.

- [x] Создать страницы Home, Contribute, NewJob, JobDetails и компоненты PlanPreview, UploadPicker, ResultViewer, JobTimeline.
- [x] Сделать адаптивную навигацию, API client, обработку ошибок и авторизации.
- [x] Home: два основных действия — Run a task и Contribute compute.
- [x] Contribute: команда подключения для ОС, реальные preflight-результаты, бюджет ресурсов, явное согласие на GPU, Drain/Stop; понятный следующий шаг при отсутствии Docker.
- [x] NewJob: запрос, выбор рецепта или загрузка своего проекта ZIP/папкой, явный выбор входных данных.
- [x] Показывать список и размеры файлов, исключённые секреты и выбранные фрагменты, которые будут переданы внешнему LLM.
- [x] Подключить resumable uploads по серверному контракту.
- [x] Отображать асинхронное планирование, уточняющие вопросы и ответы с привязкой к plan_revision.
- [x] Preview: что запустится, где будут данные, какие результаты получим, CPU/GPU и политика восстановления; неизвестную длительность показывать как неизвестную.
- [x] Разрешать Run только для актуальной ready-ревизии; сохранять один idempotency key при повторном нажатии.
- [x] JobDetails: этапы, accepted/failed/total units, очередь, ожидание GPU, повторы, ошибки элементов, отмена и логи с отметкой об усечении.
- [x] Восстанавливать наблюдение после перезагрузки страницы и разрыва SSE через cursor/snapshot; не создавать повторный job.
- [x] Показывать частичный результат явно и давать скачивать готовые файлы, включая model.pt.
- [x] Для поиска показывать найденные отрывки и источники; для обучения — метрики и скачиваемую модель.
- [x] Показывать понятные действия при потере сети, нехватке ресурсов и ошибках планировщика.
- [x] Добавить Playwright-сценарии: двойной Run, stale preview, revision ответов, восстановление SSE, partial result, скачивание model.pt.
- [ ] Проверить мобильный и десктопный путь с человеком, который не писал UI.

## 2. Панель пула — apps/console

Результат: видно, какие устройства работают, что они реально сделали и какой эффект дал общий пул.

- [x] Создать страницы Overview, Nodes, Jobs, Benchmarks и компоненты Topology, ThroughputChart, AttemptTimeline, CostBreakdown.
- [x] Overview: устройства и роли, CPU/GPU, готовность, текущая работа, принятые полезные единицы, очередь и восстановление.
- [x] Строить топологию и анимацию только по реальным событиям.
- [x] Детали узла и задачи: попытки, ошибки, повторы, принятие результата, timestamps, input/output digests, ресурсы и связь.
- [x] Показывать административные действия только с соответствующими правами; обрабатывать отказ сервера.
- [x] Различать ноль, отсутствующее измерение и оценку; обрабатывать empty/offline/partial/stale состояния.
- [x] Benchmarks: выбор workload, входных данных, устройств, concurrency, batch policy и повторов; импорт campus benchmark-local.
- [x] Разделить standalone, single-node-through-service и pool серии; показывать происхождение локальных timings.
- [x] При изменении параметров создавать новый запуск; объединять только сопоставимые серии в experiment_id.
- [x] Графики: Pi/Ppool, efficiency/speedup, CPU/GPU utilization и transfer/queue/start/compute/verify/aggregate.
- [x] Показывать единицы, cold/warm, число повторов, медиану, разброс, окно измерения, gaps и bottleneck.
- [x] Credits/cost: версия тарифных единиц, accepted units, LLM, хранение, дополнительная энергия и поддержка; оценки сопровождать предположениями, credits не выдавать за денежный заработок.
- [x] Добавить UI-тесты offline/partial/stale events и прав на действия.

## 3. Benchmark tools — tools/benchmark

- [x] Создать run.py: вызывать benchmark API и сохранять ответы по --base-url, --config, --out; не менять БД, leases или показатели напрямую.
- [x] Создать report.py: HTML/Markdown отчёт и экспорт JSON/CSV из сохранённых данных.
- [x] Считать только уникальные корректно принятые полезные единицы; исключать дубли и failed из числителя.
- [x] Сравнивать одинаковые workload, качество, распределение входов, ресурсы и единицы; в sum(Pi) включать ровно compute nodes выбранной серии.
- [x] Вычислять efficiency = Ppool / sum(Pi) * 100% и speedup = Ppool / max(Pi).
- [x] При отсутствующем или нулевом baseline показывать «недостаточно данных»; efficiency >100% сохранять с флагом проверки методики.
- [x] Разделить normal throughput и fault-injection, end-to-end и steady-state, cold start и planning time/cost.
- [x] Сохранять manifest/digests, настройки, итоговый SHA и raw metrics; раскрывать кеш моделей и отключать results cache для evaluation.
- [x] Добавить test_report.py: дубли/failed, состав compute nodes, missing/zero baseline, несовместимые units, efficiency >100%.
- [ ] После реальных измерений пересобрать отчёт из raw данных; минимум три сопоставимых повтора, без выдуманного ускорения.

## 4. Материалы сдачи — docs/submission

- [ ] inventory.md: фактические устройства и compute/control/validation роли, включая координатор; данные получить от runner/control-plane.
- [x] usage.md: точка входа, запуск и inputs/outputs для поиска по документам и обучения своего кода.
- [ ] results.md и results/: реальные JSON/CSV, методика, производительность, utilization, стоимость и разбор bottleneck.
- [x] product.md: одна страница о лаборатории/курсе/команде, задаче пользователя и пользе приватного пула.
- [x] business.md: не более страницы о пилотах, альтернативах, привлечении участников, стоимости, доверии и сетевых барьерах; подписку обозначить гипотезой.
- [ ] Если доступны потенциальные пользователи, проверить спрос короткими беседами; иначе явно отметить отсутствие проверки.
- [x] demo.md: подключение ноутбука → новый корпус → AI preview → индекс минимум на двух устройствах → поиск с источниками → остановка CPU-worker с согласия владельца → повтор незавершённой работы → отчёт.
- [x] Добавить в демо свой train.py, веса/метрики, восстановление checkpoint и честное ожидание GPU.
- [x] Подготовить ссылки на материалы для владельца control-plane: docs/submission/README.md и integration-handoff.md. Корневой README не изменён; сообщения другим людям не отправлялись.

## 5. Проверки и интеграция

- [x] Portal: npm --prefix apps/portal ci; npm --prefix apps/portal run build; npm --prefix apps/portal run test:e2e с fixture API.
- [x] Console: npm --prefix apps/console ci; npm --prefix apps/console run build; npm --prefix apps/console run test:e2e.
- [ ] Reports: uv run --project server pytest tools/benchmark/tests -q после появления server foundation. Использовать stdlib и согласованный pytest; новые server dependencies согласовывать с владельцем.
- [ ] Первая интеграция: показать в UI реальные события одного батча между двумя физическими устройствами, upload/receipt/download.
- [ ] Вторая интеграция: проверить LLM preview → own-code run → model.pt; map job на двух compute nodes и восстановление после отключения CPU-worker.
- [x] Изменять только свои части в нашей ветке; несовместимые API-изменения оставить финальному merge-агенту по указанию пользователя.
- [x] Подготовить обе UI-части в объединённой ветке к передаче merge-агенту; порядок плана и точки подключения описаны в integration-handoff.md.
- [ ] На итоговом интеграционном SHA повторить сквозные проверки и формальные измерения; записать SHA в отчёт.

Готово, когда пользователь проходит подключение и запуск без подсказок, результаты подтверждены сервером, панель показывает реальные данные, а отчёт и материалы позволяют воспроизвести демонстрацию.

## 6. Итоговое локальное ревью

- [x] Сверить реализацию обеих частей с актуальным планом main, проверить все именованные артефакты и ограничения владения.
- [x] Проверить состояния ошибок, permissions, загрузки, revision/idempotency, SSE replay/reset, отчёты, зависимости и desktop/mobile rendering; исправить найденное.
- [x] Зафиксировать результаты и внешние ограничения в docs/submission/review.md.
- [x] Отправить итоговый review-коммит только в codex/portal-console и проверить remote SHA.
