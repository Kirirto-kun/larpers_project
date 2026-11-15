# larpool: бриф для агента презентации — Investment, 30–60 секунд

## Задача

Добавь **один слайд для 30 секунд или два для 60 секунд**. Инструкции ниже — на русском; готовый текст слайдов и выступления — на английском. Название бренда: **larpool**.

Это краткий блок **Investment: 35/100 баллов**, а не полная презентация проекта. Если 30–60 секунд выделены на весь проект, используй один объединённый слайд и короткую реплику из раздела 4; оставшееся время отдай работающему сценарию и реальным результатам. Не добавляй отдельные слайды про рынок, архитектуру или девять конкурентов.

## 1. Критерии, которые обязательно закрыть

| Официальный критерий | Баллы | Что должно прозвучать или быть видно |
|---|---:|---|
| Commercial Use Cases & Adoption Potential | 20 | Первые пользователи, альтернативы, привлечение и расширение |
| Cost-Effectiveness & Sustainability | 15 | Оборудование, электричество, сеть, обслуживание, разрешения и доверие |

Источник: [официальный бриф, страницы 3–5](../briefs/D_Campus_Edge_Fabric_EN.pdf). Regular/consistent users и secondary users не являются отдельными строками оценки: показываем их через повторяемую потребность и расширение.

## 2. Готовое содержание: два слайда / до 60 секунд

Используй **две небольшие таблицы**, по одной на слайд. Они заменяют общие списки преимуществ: первая показывает выбор клиента, вторая — затраты. Не добавляй их третьим и четвёртым слайдами.

### Слайд 1 — Customer, alternatives & business model

**Заголовок:** `Who pays, and why choose larpool?`

**Строка над таблицей:** `First users: research teams with recurring batches. Potential buyer: lab lead.`

| Option | What the team gets | Decision / trade-off |
|---|---|---|
| One workstation | Local execution | Simplest baseline; one machine's capacity |
| Runpod / AutoDL | Rented GPU capacity | Rental, storage and setup costs |
| Salad Private / HTCondor | Existing own-device pooling | Pooling already exists |
| **larpool — pilot** | **Guided team batch workflow** | **Ease and cost advantage to validate** |

**Строки под таблицей:**

- `Acquire & expand: lab-led pilot → adjacent labs → courses.`
- `Revenue hypothesis: paid setup + monthly private-pool support.`

Это сравнение моделей использования, а не рейтинг производительности. Не ставь конкурентам крестики в колонках privacy, AI, verification или own devices: отсутствия этих функций мы не установили. Объединение Salad и HTCondor в одной строке означает только общий вариант «использовать готовое решение для своего пула», а не одинаковые продукты или лицензии.

**Заметки выступающего, не переносить целиком на слайд:**

- Первый сегмент — небольшая знакомая лаборатория или исследовательская команда с реальной повторяющейся задачей. «Все студенты» — слишком широкий сегмент.
- Кандидат первого сценария — индексация разрешённых документов с полезным поиском по исходным материалам. Использовать этот пример, только если он соответствует демонстрируемому workload; иначе назвать фактически работающую пакетную задачу.
- Канал привлечения — руководитель лаборатории/TA и существующая группа команды. Расширение начинается с соседних лабораторий, затем курсов.
- Прямые альтернативы: Salad Private Cloud, HTCondor, BOINC. Объединение устройств, приватный пул, AI и учёт вклада сами по себе не уникальны. Сравнительное удобство larpool — гипотеза для проверки.

### Слайд 2 — Cost plan & sustainability

**Заголовок:** `Support effort determines the economics`

**Обязательная заметная подпись:** `Illustrative monthly budget — assumptions, not measured costs.`

| Cost item | USD / month |
|---|---:|
| Electricity | $3.60 |
| Hardware wear reserve | $4.80 |
| Coordinator, storage/network & AI | $6.00 |
| Operator time | $40.00 |
| **Total economic cost** | **$54.40** |

**Основание прямо под таблицей:** `4 existing devices · 160 device-hours/month · 4 operator hours/month.`

**Две короткие нижние строки:**

- `Validate: total cost per correct result + repeat usage.`
- `Trust: owner permission, voluntary participation, non-sensitive pilot data.`

Не называй $54.40 тарифом подписки, фактическим счётом, доказанной экономией или стоимостью определённого объёма результатов. Не сопоставляй эту сумму с облачным GPU-часом. Это условная полная стоимость эксплуатации одного пула при перечисленных ниже предположениях.

**Предположения для speaker notes; сохранить вместе со слайдом:**

| Параметр | Условное значение / расчёт |
|---|---|
| Устройства и время | 4 уже имеющихся совместимых устройства × 40 часов/месяц = 160 device-hours; работоспособность этого состава требует проверки |
| Энергия | Дополнительная мощность всего устройства 0.15 kW × 160 часов × $0.15/kWh = $3.60 |
| Износ | Условный резерв $0.03/device-hour × 160 = $4.80; не включает покупку нового компьютера |
| Инфраструктура и AI | Координатор, хранение и сеть: условно $5; внешний AI: условно $1 |
| Обслуживание | 4 часа труда × условные $10/час = $40, даже если в пилоте никто не выплачивает зарплату |
| Не включено | Первичное подключение, разработка продукта, привлечение клиентов, налоги и компенсации сверх уже учтённых энергии/износа |
| Валюта и статус | USD для иллюстрации; это не тариф кампуса, не зарплата команды и не проверенный расход. Для рабочего бюджета заменить реальными местными значениями |

**Бизнес-логика, объяснить в заметках:** покупатель — руководитель лаборатории/курса; предложение — настройка частного пула, поддержанные сценарии, обновления и поддержка. Сначала бесплатный ограниченный пилот, затем проверка готовности оплачивать внедрение и сопровождение. Финальная цена и продажи пока не подтверждены.

Не смешивать стоимость пула и расходы компании: в этом примере энергия и износ составляют $8.40 у владельцев устройств. Если larpool сам оплачивает инфраструктуру/AI и весь труд оператора, его прямые затраты обслуживания — $46/месяц. Это ещё не полная себестоимость бизнеса: к ней добавятся подключение, разработка и привлечение. Если работу выполняет лаборатория, распределение расходов будет другим. Нельзя вычитать все $54.40 из выручки larpool, не объяснив, кто что оплачивает.

Чувствительность для ответа на вопросы: при 1 часе поддержки полная стоимость примера — $24.40; при 8 часах — $94.40. Это показывает, почему самостоятельная установка и понятная диагностика влияют на бизнес. Объём корректных результатов неизвестен, поэтому стоимость единицы пока считается формулой, а не выдуманным числом.

**Заметки выступающего:**

- Сравнивать одинаковую задачу и качество результата: лучший одиночный компьютер → пул → доступное облако. Часы разных GPU не эквивалентны.
- Главные риски: настройка Docker/WSL/CUDA, поддержка, сеть и уход устройств. В затраты включать повторы и неуспешные попытки; в результат — только уникальные принятые единицы.
- Ресурсы предоставляются с разрешения владельца и возможностью прекратить участие. Реальную работу прекращения на клиенте нужно подтвердить, а не вывести из серверного статуса.
- Приватный пул не скрывает данные задачи от владельца вычисляющего устройства. Внешний AI-планировщик может получать выбранное содержимое. Для первого пилота — несекретные разрешённые данные.
- Плата за установку/поддержку — предлагаемая модель. Нет подтверждённых продаж или готовности платить.

## 3. Готовое содержание: один объединённый слайд / около 30 секунд

**Заголовок:** `larpool: a shared compute workspace for research teams`

Сетка 2 × 2, без дополнительных таблиц:

| Блок | Текст на слайде |
|---|---|
| Users & growth | `Recurring research batches. Lab pilot → adjacent labs → courses.` |
| Alternatives & focus | `Workstation / cloud / existing pools. Our hypothesis: simpler team workflows.` |
| Economics | `Paid setup + monthly support: hypothesis. Illustrative pool cost: ~$54/month; support dominates.` |
| Trust & risks | `Owner permission; voluntary participation; non-sensitive pilot data. Setup and availability are key risks.` |

**Нижняя строка:** `Pilot proposal — demand, savings and retention are not yet validated.`

Если показываешь ~$54, рядом обязательно оставь `Estimate: 4 devices, 160 device-hours, 4 support hours/month; excludes initial setup.` Полные предположения из раздела 2 перенеси в speaker notes. Если это не помещается читаемо, убери сумму и оставь `Measure full cost per correct result`; не скрывай оговорку мелким шрифтом. Для 30 секунд обе подробные таблицы остаются в резерве.

## 4. Готовый текст выступления

Тайминг ориентировочный: проверить вслух. На экране — короткие блоки выше; абзацы ниже поместить только в speaker notes.

**Около 30 секунд, один слайд:**

> Larpool targets research teams with recurring batch jobs. Alternatives are workstations, cloud GPUs and existing pools. We start with lab pilots, then adjacent labs and courses. Our business hypothesis is paid setup and support. An illustrative four-device budget is about fifty-four dollars monthly, mostly operator time; actual costs need measurement. Participation requires owner permission and non-sensitive data. Demand and savings remain unvalidated.

**Около 60 секунд, два слайда; переключить после первого абзаца:**

> Our first users are research teams with recurring batch tasks. They can use a workstation, rent GPUs, or deploy existing pools like Salad and HTCondor. We aim to simplify the team workflow. We start through lab leads, then adjacent labs and courses. Our revenue hypothesis is paid setup and monthly pool support.
>
> Our illustrative budget for four existing devices is about fifty-four dollars per month, including forty dollars of operator time. It covers energy, wear, infrastructure and AI, but excludes initial setup. These are assumptions, not measured costs or our subscription price. We will measure cost per correct result and repeat usage. Owner permission, voluntary participation and non-sensitive pilot data address initial trust concerns; installation and variable availability remain key risks.

**Если 30–60 секунд — весь питч проекта: реплика Investment на 10–15 секунд.** Остальные критерии покрыть реальной демонстрацией и измерениями; этот файл их не заменяет.

> We target research teams with recurring batches, starting through lab-led pilots. We will test paid support, cost per correct result and voluntary participation, against existing workstation, cloud and pooling alternatives.

## 5. Правила для агента: что добавлять и чего не выдумывать

- На слайдах отделяй **предлагаемый пилот и бизнес-модель** от результатов. Не называй запланированные команды или устройства действующими клиентами.
- Если появились свежие подтверждённые результаты, строку о непроверенных результатах можно заменить одним сильным фактом: реальный workload, число физических исполнителей, измеренный выигрыш или повторные пользователи. Укажи источник замера; если факта нет, оставь пометку о гипотезах. Подпись об условном бюджете и его предположения сохраняй, пока нет настоящих замеров затрат. Не вставляй незаполненные placeholders.
- В доступном [отчёте проверки сервера](../../server/VERIFICATION.md) зафиксирован один физический Mac и checksum smoke. Это не доказательство полезного multi-device ML benchmark. Более новые результаты коллеги должны иметь отдельное подтверждение.
- Не обещай «быстрее/дешевле облака», гарантированную доступность, объединённую VRAM, отсутствие внешней передачи данных или уникальность механизма pooling без доказательств.
- Условные **$54.40/месяц** разрешены только как явно подписанный сценарий из раздела 2, с доступными предположениями. Это не прайс продукта и не фактические затраты. Не добавляй рекламные GPU-тарифы, неподтверждённый TAM, выдуманную выручку или прогноз прибыли.
- Используй стиль существующей презентации: крупный заголовок, короткие подписи, один акцентный цвет. Никаких архитектурных схем и полной конкурентной матрицы в этом блоке. Не называть «35/100» полученной оценкой — это вес Investment в брифе.

## 6. Источники для speaker notes / справочного слайда

Проверены в исследовании 15.11.2026. На основной слайд не выводить длинные URL.

- [Официальный бриф, стр. 3–5](../briefs/D_Campus_Edge_Fabric_EN.pdf): критерии, допустимость предположений, коммерческое обоснование.
- [Salad Private Cloud](https://salad.com/private-cloud/): существующий частный пул на оборудовании организации.
- [HTCondor](https://htcondor.readthedocs.io/en/26.x/admin-manual/introduction-admin-manual.html): собственный пул, очередь и правила владельца устройства.
- [BOINC](https://boinc.berkeley.edu/boinc_a_platform_for_volunteer_computing.pdf): добровольные вычисления и проверка результатов.
- [Colab FAQ](https://research.google.com/colaboratory/faq.html): notebook-альтернатива и ограничения ресурсов.
- [Runpod RTX 4090](https://www.runpod.io/gpu-models/rtx-4090): арендуемые GPU как альтернатива своему парку; тарифы в слайдах не приводить.
- [AutoDL quick start](https://www.autodl.com/docs/quick_start/) и [billing](https://www.autodl.com/docs/price/): выбор GPU/образа и расходы аренды/хранения.

**Готовность блока:** критерии 20 + 15 покрыты; слайдов не больше двух; текст проговорён за доступное время; неподтверждённые показатели не представлены как результаты.
