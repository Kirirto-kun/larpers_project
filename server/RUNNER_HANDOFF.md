# Передача коллеге: runner и физические проверки

Серверная ветка: `codex/control-plane`. Источник истины — [OpenAPI](../contracts/openapi-v1.yaml), [JSON Schema](../contracts/schemas/) и сгенерированные DTO. `python3 contracts/generate.py --target go` создаёт игнорируемый `runner/internal/generated/api.go`; Python/TS доступны тем же генератором. Результаты серверных проверок: [VERIFICATION.md](VERIFICATION.md).

## Стык runner

- Получить credential через одноразовое device invitation. Создать node session с protocol version 1 и обнаруженными capabilities. Передавать bearer и `X-Node-Session` во всех запросах worker. Повторный join/новая session отзывает старую authority.
- `POST /api/v1/work/next` возвращает Assignment или 204. Сохранять исходные item IDs; `started`, `renew`, `fail`, `release`, `complete`, `checkpoints` передают generation и lease token. Deadline/remaining lease вычисляет сервер. Локальный watchdog должен прекратить вычисление при потере аренды, даже без связи с координатором.
- Download доступен только для входов актуальной assignment и совместимого checkpoint. Cache всегда адресуется SHA-256. Optional `cached_artifact_digests` и `transfer_bytes_per_second` — ограниченные подсказки для scheduler; worker проверяет bytes/checksum независимо от подсказок. Образы подготовлены заранее и закреплены digest.
- `POST /uploads` требует общий SHA-256 и размер; для output/checkpoint нужны attempt ID и proof. Каждая часть содержит `X-Content-SHA256`; status возвращает `parts[]`. Finalize возвращает Artifact с `id`, а не `artifact_id`. Не передавать ID незавершённого upload в completion.
- Перед complete/fail/release остановить workload: сервер освобождает вычислительный бюджет по этому подтверждению. Cancel/Stop/session replacement немедленно запрещают acceptance, но сохраняют ресурсную резервацию до старого lease deadline.
- Complete возвращает 202 с durable receipt, затем выполняется серверная verification. Потеря ACK: повторить неизменённый completion payload с той же authority или запросить attempt status. Повтор принятой квитанции не начисляет units второй раз. Новый payload с тем же completion конфликтует.
- Checkpoint — ZIP с `campus-checkpoint.json` и всеми файлами из manifest; точная схема находится в `contracts/schemas/checkpoint-manifest.schema.json`. Catalog adapter объявляет `checkpoint_files` — например веса, optimizer, RNG, scheduler/dataloader state. Их согласованность при сохранении обеспечивает workload. После finalize обновить pointer proof + compatibility digest + возрастающей sequence. Повтор старой sequence не откатывает более новый pointer.
- Opt-in `adaptive_batches=true` допустим только если адаптер корректно исполняет любой выданный поднабор item IDs. Новая задача может иметь другой размер; повтор существующей всегда сохраняет item IDs. При OOM уменьшать внутренний microbatch. `resume_policy=unsupported` запрещает restart.
- Benchmark `concurrency` — число одновременно исполняемых задач **на одном узле** и должно совпадать в standalone и service. Состав, runtime, units, quality, model digest, batching, warm/cold и timing scope должны быть сопоставимы. Время standalone — сообщённое устройством; оно не выдаётся за независимое серверное измерение.

## Проверки на разрешённых устройствах

1. Зафиксировать inventory: ОС, arch, CPU/RAM, GPU/VRAM/driver, Docker, адрес и роли compute/control/validation. Проверить настоящий CUDA kernel и корректный отказ при отсутствии совместимой GPU.
2. С целевого SSID проверить доверенный TLS, protocol handshake, upload/download и reconnect до запуска demo. Экспортировать CA своей установки; секретные credentials хранить отдельно от Git.
3. Пройти вертикальный путь реальным Go runner. Сопоставимый HTTP/Docker harness уже находится в `infra/smoke.py`; `--ai` включает настроенный живой DeepSeek.
4. Выполнить embeddings/index на минимум двух физических compute nodes и затем отдельный search job. Подтвердить полезный acceptance от обоих узлов; регистрации двух sessions на одном ноутбуке недостаточно.
5. Отключить CPU worker с разрешения владельца: другая generation должна завершить исходные item IDs без двойного acceptance/ledger. Проверить Drain и Stop отдельно.
6. Запустить собственный training-код, скачать model/metrics; прервать GPU worker, увидеть waiting_for_capacity, вернуть совместимый GPU и восстановить полный checkpoint. Проверить фактическое сохранение шага и состояния обучения.
7. Собрать минимум три matched standalone/single-node-service/pool повтора на новых данных. Сохранить raw observations, workload/runtime digests, итоговый integration SHA и объяснить coordinator/validation overhead.
8. Скопировать backup на отдельное устройство и восстановить без исходных volumes. Локальный restore уже проверен; сохранность при физической потере ноутбука требует внешней копии.

Portal/Console находятся в самостоятельной ветке `codex/portal-console`. Их production builds и доставка по одному HTTPS origin проверены. Подключение generated DTO и авторизованный браузерный сценарий остаются задачей финального интегратора, согласно handoff этой ветки.
