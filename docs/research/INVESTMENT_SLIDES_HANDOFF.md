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

### Слайд 1 — Users & adoption

**Заголовок:** `Shared compute for small research teams`

Поставь четыре коротких блока; подписи слева, содержание справа:

| Подпись | Текст на слайде |
|---|---|
| First → regular users | `Research teams → recurring document / data batches` |
| Alternatives | `One workstation · Cloud GPUs · Existing pools` |
| Our hypothesis | `A simpler workflow on the team's existing CPU/GPU devices` |
| Acquire → expand | `Lab-led pilot → adjacent labs → courses` |

**Заметки выступающего, не переносить целиком на слайд:**

- Первый сегмент — небольшая знакомая лаборатория или исследовательская команда с реальной повторяющейся задачей. «Все студенты» — слишком широкий сегмент.
- Кандидат первого сценария — индексация разрешённых документов с полезным поиском по исходным материалам. Использовать этот пример, только если он соответствует демонстрируемому workload; иначе назвать фактически работающую пакетную задачу.
- Канал привлечения — руководитель лаборатории/TA и существующая группа команды. Расширение начинается с соседних лабораторий, затем курсов.
- Прямые альтернативы: Salad Private Cloud, HTCondor, BOINC. Объединение устройств, приватный пул, AI и учёт вклада сами по себе не уникальны. Сравнительное удобство larpool — гипотеза для проверки.

### Слайд 2 — Economics & trust

**Заголовок:** `Measure value per correct result`

| Подпись | Текст на слайде |
|---|---|
| Buyer / model hypothesis | `Lab lead · Private-pool setup & support` |
| Full cost | `Hardware wear + power + network/storage + AI + operations` |
| Measure | `Total cost ÷ unique accepted results` |
| Trust / availability | `Owner permission · Voluntary participation · Non-sensitive pilot data · Variable availability` |

**Нижняя строка:** `Pilot proposal — demand, savings and retention are not yet validated.`

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
| Economics | `Test paid pool support. Measure cost per correct result: hardware, power, network, operations.` |
| Trust & risks | `Owner permission; voluntary participation; non-sensitive pilot data. Setup and availability are key risks.` |

**Нижняя строка:** `Pilot proposal — demand, savings and retention are not yet validated.`

## 4. Готовый текст выступления

Тайминг ориентировочный: проверить вслух. На экране — короткие блоки выше; абзацы ниже поместить только в speaker notes.

**Около 30 секунд, один слайд:**

> Larpool targets research teams with recurring batch jobs. Alternatives are workstations, cloud GPUs and existing pools. Our hypothesis is simpler team workflows. We start with lab pilots, then adjacent labs and courses. We will test paid support and cost per correct result, including hardware wear, power, bandwidth and operations. Participation requires owner permission and non-sensitive pilot data. Demand and savings remain unvalidated.

**Около 60 секунд, два слайда; переключить после первого абзаца:**

> Larpool targets small research teams with recurring batch tasks, such as indexing permitted documents. Their alternatives are a workstation, rented GPUs or existing pools like Salad and HTCondor. Our hypothesis is a simpler complete workflow on the team's CPU and GPU devices. We would recruit through a lab lead, then expand to adjacent labs and courses after repeat use.
>
> The business hypothesis is paid setup and support for private pools. We will compare cost per correct result, including hardware wear, electricity, bandwidth, storage, AI and operator time. Setup effort and unreliable device availability are key risks. Participation requires explicit owner permission; initial data is non-sensitive. We still need real multi-device measurements, returning users and willingness-to-pay evidence before claiming commercial advantage.

**Если 30–60 секунд — весь питч проекта: реплика Investment на 10–15 секунд.** Остальные критерии покрыть реальной демонстрацией и измерениями; этот файл их не заменяет.

> We target research teams with recurring batches, starting through lab-led pilots. We will test paid support, cost per correct result and voluntary participation, against existing workstation, cloud and pooling alternatives.

## 5. Правила для агента: что добавлять и чего не выдумывать

- На слайдах отделяй **предлагаемый пилот и бизнес-модель** от результатов. Не называй запланированные команды или устройства действующими клиентами.
- Если появились свежие подтверждённые результаты, замени нижнюю строку одним сильным фактом: реальный workload, число физических исполнителей, измеренный выигрыш или повторные пользователи. Укажи источник замера; если факта нет, оставь пометку о гипотезах. Не вставляй незаполненные placeholders.
- В доступном [отчёте проверки сервера](../../server/VERIFICATION.md) зафиксирован один физический Mac и checksum smoke. Это не доказательство полезного multi-device ML benchmark. Более новые результаты коллеги должны иметь отдельное подтверждение.
- Не обещай «быстрее/дешевле облака», гарантированную доступность, объединённую VRAM, отсутствие внешней передачи данных или уникальность механизма pooling без доказательств.
- Не добавляй условные **$54.40/месяц**, рекламные GPU-тарифы, TAM, график выручки и прогноз прибыли: в коротком питче они отвлекают и легко воспринимаются как измеренные показатели.
- Используй стиль существующей презентации: крупный заголовок, короткие подписи, один акцентный цвет. Никаких архитектурных схем и полной конкурентной матрицы в этом блоке. Не называть «35/100» полученной оценкой — это вес Investment в брифе.

## 6. Источники для speaker notes / справочного слайда

Проверены в исследовании 15.11.2026. На основной слайд не выводить длинные URL.

- [Официальный бриф, стр. 3–5](../briefs/D_Campus_Edge_Fabric_EN.pdf): критерии, допустимость предположений, коммерческое обоснование.
- [Salad Private Cloud](https://salad.com/private-cloud/): существующий частный пул на оборудовании организации.
- [HTCondor](https://htcondor.readthedocs.io/en/26.x/admin-manual/introduction-admin-manual.html): собственный пул, очередь и правила владельца устройства.
- [BOINC](https://boinc.berkeley.edu/boinc_a_platform_for_volunteer_computing.pdf): добровольные вычисления и проверка результатов.
- [Colab FAQ](https://research.google.com/colaboratory/faq.html): notebook-альтернатива и ограничения ресурсов.

**Готовность блока:** критерии 20 + 15 покрыты; слайдов не больше двух; текст проговорён за доступное время; неподтверждённые показатели не представлены как результаты.
