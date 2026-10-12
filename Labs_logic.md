# Логика лабораторных работ 1–3

## Содержание
1. [Общая архитектура](#1-общая-архитектура)
2. [Общие технические решения](#2-общие-технические-решения)
3. [ЛР №1 — Топ-10 прогульщиков по теме](#3-лр-1--топ-10-прогульщиков-по-теме)
4. [ЛР №2 — Вместимость аудиторий по курсу](#4-лр-2--вместимость-аудиторий-по-курсу)
5. [ЛР №3 — Аудит профильных часов по группе](#5-лр-3--аудит-профильных-часов-по-группе)

---

## 1. Общая архитектура

```
Клиент
  │  HTTP POST /lab0N/get_report  (Bearer JWT)
  ▼
API Gateway  ─── проверяет JWT, проксирует запрос
  │
  ▼
lab0N (FastAPI)
  ├── routes/reports.py      — приём HTTP, валидация тела, вызов сервиса
  ├── services/reports_service.py — бизнес-логика, оркестрация БД
  └── models/web/report.py   — Pydantic-схемы запроса и ответа
```

Каждая лаба — отдельный Python-процесс (контейнер).  
Все три реализуют одну и ту же HTTP-схему: `POST /get_report` с JSON-телом.  
Общий принцип: **каждое хранилище используется ровно для того, для чего оно оптимально**.

### Роли хранилищ

| Хранилище | Сильная сторона | Роль в проекте |
|-----------|----------------|----------------|
| **OpenSearch** | Полнотекстовый поиск, русский стеммер | ЛР1: поиск лекций по термину |
| **Neo4j** | Графовые обходы, связи без JOIN | ЛР1–3: навигация по расписанию, группам, курсам |
| **PostgreSQL** | Транзакции, партиционирование, агрегаты | ЛР1–3: посещаемость, метаданные курсов, данные студентов |
| **Redis** | In-memory, O(1) по ключу, pipeline | ЛР1,3: профили студентов (HGETALL) |
| **MongoDB** | Документы, вложенные объекты | ЛР1,3: орг-иерархия университета |

---

## 2. Общие технические решения

### 2.1 Управление соединениями — lifespan

Файл: `lab0N/app/main.py`

FastAPI получает соединения через `lifespan`-контекст менеджер, а не через `startup`/`shutdown` события (устаревший способ). Все соединения сохраняются в `app.state`, чтобы роутер мог достать их через `request.app.state`.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pg_pool  = await asyncpg.create_pool(...)
    app.state.redis    = aioredis.from_url(...)
    app.state.mongo    = AsyncIOMotorClient(...)[\"university_db\"]
    app.state.neo4j    = AsyncGraphDatabase.driver(...)
    app.state.opensearch = AsyncOpenSearch(...)
    yield
    # shutdown: закрыть все соединения
```

**Почему не синглтоны на уровне модуля?** Потому что `asyncpg.create_pool()` — это корутина; её нельзя вызвать вне async-контекста. Lifespan гарантирует, что event loop уже запущен.

**Пул asyncpg (min=2, max=10):** PostgreSQL используется для тяжёлых JOIN-запросов, нужен пул соединений. Остальные клиенты (Redis, MongoDB, OpenSearch) сами управляют своим пулом.

**NEO4J_AUTH:** Если значение `"none"` — передаём `auth=None`. Иначе `split("/", 1)` → `(user, password)`. Это нужно для совместимости с локальными конфигурациями без авторизации.

**OpenSearch (только ЛР1):** `use_ssl=True, verify_certs=False` — TLS включён (security plugin активен), но самоподписанный сертификат dev-среды не верифицируем.

### 2.2 Слой роутера

Файл: `lab0N/app/api/routes/reports.py`

Роутер отвечает только за HTTP-представление. Никакой бизнес-логики:

```python
@router.post("/get_report", response_model=ReportResponse)
async def get_report(request: Request, body: ReportRequest) -> ReportResponse:
    result = await reports_service.get_...(request.app.state, body.*)
    if result is None:
        raise HTTPException(status_code=404, ...)
    return result
```

- `request: Request` — единственный способ получить `app.state` в FastAPI без DI-контейнера.
- `response_model=ReportResponse` — FastAPI автоматически сериализует и валидирует ответ по Pydantic-схеме.
- `None` возвращается сервисом, когда данные не найдены (например, неизвестный group_id). Роутер превращает это в HTTP 404.

### 2.3 Паттерн преобразования UUID

Все внутренние ID хранятся как `UUID` (uuid4) в Python. При передаче в **asyncpg** используем нативные `UUID`-объекты — драйвер сам конвертирует их в PostgreSQL `uuid`. При передаче в **Neo4j** используем `str(uuid)`, потому что Neo4j хранит ID как строку. Redis-ключ — `f"student:{str_id}"`.

---

## 3. ЛР №1 — Топ-10 прогульщиков по теме

**Файлы:**
- `lab01/app/api/services/reports_service.py`
- `lab01/app/models/web/report.py`
- `lab01/app/api/routes/reports.py`
- `lab01/app/main.py`

### 3.1 Задача

Найти 10 студентов с наименьшим процентом посещаемости лекций, содержащих заданный поисковый термин, за указанный период.

### 3.2 Входные параметры (`ReportRequest`)

```python
class ReportRequest(BaseModel):
    term: str       # поисковый запрос, например "машинное обучение"
    date_from: date # начало периода
    date_to: date   # конец периода
```

### 3.3 Выходные данные (`ReportResponse`)

```python
class ReportResponse(BaseModel):
    term: str
    date_from: date
    date_to: date
    generated_at: datetime
    students: list[StudentAttendanceRecord]  # ≤ 10 записей
    total_found: int

class StudentAttendanceRecord(BaseModel):
    student_id: UUID
    last_name, first_name, middle_name: str  # из Redis
    email, phone: str                         # из Redis
    group_name: str                           # из Neo4j
    enrollment_date: date                     # из PostgreSQL
    attendance_percent: float                 # вычисляется
    total_lecture_events: int                 # из Neo4j (кол-во занятий)
    present_count: int                        # из PostgreSQL
    specialty_name: str                       # из Neo4j
    department_name, institute_name, university_name: str  # из MongoDB
    report_period: str                        # "date_from..date_to"
    term: str
```

### 3.4 Алгоритм (5 шагов)

#### Шаг 1 — OpenSearch: поиск лекций по термину

Функция: `_search_lectures_opensearch(client, term)`

```python
resp = await client.search(
    index="lectures",
    body={
        "query": {
            "multi_match": {
                "query": term,
                "fields": ["title^3", "annotation^2", "materials_text"],
                "type": "best_fields",
                "operator": "or",
            }
        },
        "_source": ["lecture_id"],
        "size": 500,
    },
)
return [hit["_source"]["lecture_id"] for hit in resp["hits"]["hits"]]
```

**Почему OpenSearch?** Лекции имеют текстовые поля (title, annotation, materials_text). Для нечёткого поиска по содержимому реляционная БД с LIKE неэффективна — нет стемминга, нет рейтинга релевантности. OpenSearch использует русский анализатор (стандартный токенайзер → lowercase → russian_stop → russian_stemmer), поэтому запрос "машинное обучение" найдёт лекции с "машинного обучения", "обучением машин" и т.д.

**Множители полей:** `title^3` — заголовок важнее, `annotation^2` — аннотация чуть важнее тела. `best_fields` — побеждает поле с лучшим совпадением, а не среднее. `size=500` — берём максимум 500 лекций, чтобы не тащить весь индекс.

**Ранний выход:** если `lecture_ids` пустой список — возвращаем пустой отчёт немедленно, без обращения к остальным БД.

#### Шаг 2 — Neo4j: студенты через граф расписания

Функция: `_find_students_neo4j(driver, lecture_ids, date_from, date_to)`

```cypher
UNWIND $lecture_ids AS lid
MATCH (l:Lecture {id: lid})
MATCH (c:LectureCourse)-[:CONTAINS_LECTURE]->(l)
MATCH (c)-[:TAUGHT_BY_DEPARTMENT]->(d:Department)
MATCH (sch:Schedule)-[:FOR_LECTURE]->(l)
WHERE date(sch.scheduled_date) >= date($date_from)
  AND date(sch.scheduled_date) <= date($date_to)
MATCH (sch)-[:FOR_GROUP]->(g:StudentGroup)<-[:MEMBER_OF]-(s:Student)
MATCH (g)-[:STUDIES_SPECIALTY]->(sp:Specialty)
RETURN s.id            AS student_id,
       g.name          AS group_name,
       sp.name         AS specialty_name,
       d.id            AS department_id,
       count(DISTINCT sch) AS total_lecture_events
```

**Почему Neo4j?** Этот запрос пересекает 7 типов узлов и 6 типов рёбер. В реляционной БД это был бы JOIN из 6+ таблиц с GROUP BY — дорого. В графовой БД такой обход — нативная операция.

**UNWIND:** разворачивает список `lecture_ids` в отдельные строки — аналог `UNNEST` в SQL. Позволяет обработать все лекции одним запросом без N+1.

**`count(DISTINCT sch)`:** считаем уникальные записи расписания (одно занятие в одну дату для одной группы), а не студентов. Это и есть `total_lecture_events` — знаменатель для расчёта процента посещаемости.

**Дедупликация в Python:** один студент может появиться в нескольких строках, если лекции из разных курсов относятся к разным расписаниям. Оставляем запись с максимальным `total_lecture_events`:

```python
for row in neo4j_rows:
    s_id = row["student_id"]
    if s_id not in student_neo4j or row["total_lecture_events"] > student_neo4j[s_id]["total_lecture_events"]:
        student_neo4j[s_id] = row
```

#### Шаг 3 — PostgreSQL: подсчёт посещений

Функция: `_count_attendance_pg(pool, student_ids, lecture_ids, date_from, date_to)`

```sql
SELECT
    s.id::text                      AS student_id,
    COALESCE(att.present_count, 0)  AS present_count,
    s.enrollment_date
FROM student s
LEFT JOIN (
    SELECT a.student_id, COUNT(*) AS present_count
    FROM attendance a
    JOIN schedule sch
        ON  a.schedule_id     = sch.id
        AND a.week_start_date = sch.week_start_date
    WHERE a.student_id   = ANY($1::uuid[])
      AND sch.lecture_id  = ANY($2::uuid[])
      AND sch.week_start_date BETWEEN $3 AND $4
    GROUP BY a.student_id
) att ON att.student_id = s.id
WHERE s.id = ANY($1::uuid[])
```

**Почему LEFT JOIN, а не INNER JOIN?** Студент с нулевой посещаемостью не имеет записей в `attendance`, но должен попасть в отчёт с `present_count = 0`. COALESCE обрабатывает NULL от LEFT JOIN.

**Составной JOIN по `(schedule_id, week_start_date)`:** таблица `attendance` партиционирована по `week_start_date`. Чтобы PostgreSQL мог использовать partition pruning, предикат на `week_start_date` должен присутствовать явно — поэтому JOIN включает оба поля, а не только `schedule_id`.

**Параметры `$1::uuid[]`:** asyncpg требует явного каста при передаче массива UUID. В Python предварительно конвертируем: `[UUID(s) for s in student_ids]`.

#### Шаг 4 — Redis: профили студентов

Функция: `_get_student_profiles_redis(client, student_ids)`

```python
pipe = client.pipeline(transaction=False)
for s_id in student_ids:
    pipe.hgetall(f"student:{s_id}")
results = await pipe.execute()
```

**Почему pipeline?** Для N студентов без pipeline — N round-trip'ов к Redis. Pipeline упаковывает все команды в один TCP-запрос. Разница на 1000 студентов: ~1000 мс vs ~3 мс.

**`transaction=False`:** нам не нужна атомарность (мы только читаем разные ключи). Без транзакции Redis не открывает MULTI/EXEC и чуть быстрее.

**`decode_responses=True` в клиенте:** Redis возвращает bytes. Флаг включён при создании клиента в lifespan, чтобы сразу получать str — не нужно вручную `.decode()`.

#### Шаг 5 — MongoDB: орг-иерархия

Функция: `_get_org_lookup_mongo(db)`

```python
doc = await db["organization_structure"].find_one({})
```

**Один документ на весь университет.** MongoDB хранит всю иерархию (университет → институты → кафедры) в одном вложенном документе. Один `find_one` вместо JOIN трёх таблиц.

Из документа строим lookup-словарь `{department_id → {department_name, institute_name, university_name}}` в Python — O(D) по числу кафедр, потом доступ за O(1).

#### Шаг 6 — Сборка и сортировка

```python
pct = round(present / total * 100, 2) if total > 0 else 0.0
records.sort(key=lambda r: (r.attendance_percent, r.last_name))
top_10 = records[:10]
```

**Сортировка:** первичный ключ — `attendance_percent` по возрастанию (худшие прогульщики первыми). При равном проценте — по фамилии для детерминированного порядка.

**Защита от деления на ноль:** `total > 0 else 0.0`. Теоретически невозможно (если студент пришёл из Neo4j, у него есть занятия), но защита есть.

---

## 4. ЛР №2 — Вместимость аудиторий по курсу

**Файлы:**
- `lab02/app/api/services/reports_service.py`
- `lab02/app/models/web/report.py`
- `lab02/app/api/routes/reports.py`
- `lab02/app/main.py`

### 4.1 Задача

Для заданного курса и периода найти все занятия, рассчитать необходимую вместимость аудитории (количество студентов на каждое занятие), и вывести полную информацию о лекции.

### 4.2 Входные параметры

```python
class ReportRequest(BaseModel):
    course_id: UUID
    date_from: date
    date_to: date
```

### 4.3 Выходные данные

```python
class ReportResponse(BaseModel):
    course: CourseInfo          # метаданные курса
    period: PeriodInfo          # период запроса
    sessions: list[SessionRecord]  # каждое занятие с вместимостью
    generated_at: datetime

class SessionRecord(BaseModel):
    schedule_id: UUID
    session_date: date
    required_capacity: int      # count(students) на это занятие
    lecture_id: UUID
    title: str
    lecture_type: str
    order_number: int
    equipment_requirements: str
```

### 4.4 Алгоритм (2 шага)

#### Шаг 1 — Neo4j: занятия курса и вместимость

Функция: `_find_sessions_neo4j(driver, course_id, date_from, date_to)`

```cypher
MATCH (c:LectureCourse {id: $course_id})
MATCH (c)-[:CONTAINS_LECTURE]->(l:Lecture)
MATCH (sch:Schedule)-[:FOR_LECTURE]->(l)
WHERE date(sch.scheduled_date) >= date($date_from)
  AND date(sch.scheduled_date) <= date($date_to)
MATCH (sch)-[:FOR_GROUP]->(g:StudentGroup)<-[:MEMBER_OF]-(s:Student)
RETURN c.id            AS course_id,
       c.name          AS course_name,
       c.semester      AS semester,
       c.academic_year AS academic_year,
       c.total_hours   AS total_hours,
       sch.id          AS schedule_id,
       sch.scheduled_date AS session_date,
       l.id            AS lecture_id,
       count(DISTINCT s) AS required_capacity
ORDER BY sch.scheduled_date, sch.id
```

**Почему Neo4j?** Запрос по своей природе графовый: от курса идём к лекциям, от лекций — к расписанию, от расписания — к группам и студентам. В PostgreSQL это был бы JOIN из 5 таблиц (lecture_course → lecture → schedule → student_group → student) плюс GROUP BY.

**`count(DISTINCT s)` per `sch.id`:** одна запись в расписании = одна лекционная сессия одной группы. Количество студентов в этой группе — и есть `required_capacity`. Если одна лекция читается нескольким группам, каждая группа даёт отдельную строку с отдельным `schedule_id` и своим `required_capacity`.

**Метаданные курса из той же строки:** `c.name`, `c.semester`, `c.academic_year`, `c.total_hours` одинаковы для всех строк результата (это свойства одного узла). Берём из первой строки — не нужен отдельный запрос.

**ORDER BY:** отсортировано по дате, затем по schedule_id для стабильного порядка.

#### Шаг 2 — PostgreSQL: метаданные лекций

Функция: `_get_lecture_details_pg(pool, lecture_ids)`

```sql
SELECT id::text,
       title,
       lecture_type,
       order_number,
       equipment_requirements
FROM lecture
WHERE id = ANY($1::uuid[])
```

**Почему PostgreSQL, а не Neo4j?** В Neo4j хранится только `lecture.id` — узел идентификации для графовых связей. Содержательные атрибуты лекции (title, type, equipment) — структурированные данные без связей, они оптимально хранятся в реляционной таблице. Запрос прост: `WHERE id = ANY(...)`, один индексный поиск.

**`ANY($1::uuid[])`:** asyncpg передаёт список UUID как PostgreSQL массив. Одна операция вместо N отдельных SELECT.

#### Сборка

```python
for row in neo4j_rows:
    lid = row["lecture_id"]
    details = lecture_details.get(lid, {})
    sessions.append(SessionRecord(
        schedule_id=UUID(row["schedule_id"]),
        session_date=date.fromisoformat(str(row["session_date"])),
        ...
    ))
```

**`str(row["session_date"])`:** Neo4j может вернуть дату либо как строку `"2024-09-02"` (если хранилась как string), либо как `neo4j.time.Date`. `str()` работает для обоих случаев, `date.fromisoformat()` парсит ISO-строку.

### 4.5 Что НЕ используется в ЛР2 и почему

- **OpenSearch** — не нужен полнотекстовый поиск; курс задаётся точным UUID.
- **Redis** — нет данных о конкретных студентах, только их количество.
- **MongoDB** — нет орг-иерархии в ответе.

Поэтому `lab02/app/main.py` открывает только 2 соединения: `asyncpg.Pool` и `neo4j.AsyncDriver`. Это снижает потребление ресурсов и время старта контейнера.

---

## 5. ЛР №3 — Аудит профильных часов по группе

**Файлы:**
- `lab03/app/api/services/reports_service.py`
- `lab03/app/models/web/report.py`
- `lab03/app/api/routes/reports.py`
- `lab03/app/main.py`

### 5.1 Задача

Для заданной учебной группы вывести по каждому **профильному курсу кафедры** (`is_department_discipline = TRUE`) список всех студентов с указанием запланированных (`planned_hours`) и фактически посещённых часов (`attended_hours`). Одна посещённая лекция = 2 академических часа.

### 5.2 Входные параметры

```python
class ReportRequest(BaseModel):
    group_id: UUID
    date_from: date
    date_to: date
```

### 5.3 Выходные данные

```python
class ReportResponse(BaseModel):
    group: GroupInfo            # id, name, enrollment_year, curator
    period: PeriodInfo
    courses: list[CourseHours]  # только профильные курсы
    generated_at: datetime

class CourseHours(BaseModel):
    id: UUID
    name: str
    semester: int
    academic_year: int
    planned_hours: int          # total_hours из Neo4j/PG
    department_name: str        # кафедра-преподаватель курса
    students: list[StudentHours]

class StudentHours(BaseModel):
    student_id: UUID
    first_name, last_name, middle_name: str  # из Redis
    email, phone: str                         # из Redis
    specialty_name: str                       # из Neo4j
    department_name: str                      # из MongoDB (кафедра студента)
    institute_name: str                       # из MongoDB
    university_name: str                      # из MongoDB
    attended_hours: int                       # count(посещений) * 2
```

### 5.4 Алгоритм (4 шага + параллелизм)

#### Шаг 1 — Neo4j: группа, специальность, студенты, курсы кафедры

Функция: `_find_group_neo4j(driver, group_id)`

```cypher
MATCH (g:StudentGroup {id: $group_id})
MATCH (g)-[:STUDIES_SPECIALTY]->(sp:Specialty)
MATCH (g)<-[:MEMBER_OF]-(s:Student)
WITH g, sp, collect(DISTINCT s.id) AS student_ids
MATCH (dept:Department)-[:OFFERS_SPECIALTY {is_primary: true}]->(sp)
MATCH (c:LectureCourse)-[:TAUGHT_BY_DEPARTMENT]->(dept)
RETURN g.id          AS group_id,
       g.name        AS group_name,
       sp.name       AS specialty_name,
       dept.id       AS department_id,
       dept.name     AS department_name,
       c.id          AS course_id,
       c.total_hours AS total_hours,
       student_ids
```

**Ключевые решения запроса:**

`collect(DISTINCT s.id) AS student_ids` в промежуточном `WITH`: сначала собираем всех студентов группы, потом джойним кафедру и курсы. Без `WITH` студенты бы перемножались с кафедрами/курсами (декартово произведение), `collect` не работал бы корректно.

`OFFERS_SPECIALTY {is_primary: true}`: группа изучает специальность, которую могут предлагать несколько кафедр. `is_primary: true` означает, что эта кафедра — **родная** для специальности. Берём курсы только этой кафедры, иначе получим избыточные курсы от смежных кафедр.

Результат: N строк по числу курсов. Во всех строках `student_ids` одинаков (одна группа). Берём из первой строки.

#### Шаг 2 — PostgreSQL (3 параллельных запроса)

```python
group_meta, filtered_courses, attended = await asyncio.gather(
    _get_group_meta_pg(state.pg_pool, UUID(group_id_str)),
    _filter_dept_courses_pg(state.pg_pool, course_ids),
    _get_attended_hours_pg(state.pg_pool, student_ids, course_ids, date_from, date_to),
)
```

**Почему `asyncio.gather`?** Три запроса независимы — нет зависимостей по данным между ними. Параллельный запуск сокращает суммарное время ≈ до времени самого медленного из трёх (вместо суммы всех трёх).

**Запрос A — метаданные группы:**
```sql
SELECT enrollment_year, curator
FROM student_group WHERE id = $1
```
Neo4j хранит группу только с `id` и `name` — остальные атрибуты в PostgreSQL.

**Запрос B — фильтр профильных курсов:**
```sql
SELECT id::text, name, semester, academic_year
FROM lecture_course
WHERE id = ANY($1::uuid[]) AND is_department_discipline = TRUE
```
Это и есть ключевой бизнес-фильтр: из всех курсов кафедры оставляем только те, у которых `is_department_discipline = TRUE`. Хранится только в PostgreSQL (Neo4j не знает этого флага). Курсы, не прошедшие фильтр, не попадают в отчёт.

**Запрос C — посещённые часы:**
```sql
SELECT a.student_id::text,
       l.course_id::text,
       COUNT(*) * 2 AS attended_hours
FROM attendance a
JOIN schedule sch
    ON  a.schedule_id     = sch.id
    AND a.week_start_date = sch.week_start_date
JOIN lecture l ON sch.lecture_id = l.id
WHERE a.student_id  = ANY($1::uuid[])
  AND l.course_id   = ANY($2::uuid[])
  AND sch.week_start_date BETWEEN $3 AND $4
GROUP BY a.student_id, l.course_id
```

`COUNT(*) * 2`: одно посещение = одна лекция = 2 академических часа.

Двойной ключ JOIN `(a.schedule_id = sch.id AND a.week_start_date = sch.week_start_date)`: таблица `attendance` партиционирована по `week_start_date`. PostgreSQL применяет partition pruning только если предикат на ключ партиции присутствует в WHERE или JOIN-условии. Без `week_start_date` в JOIN — полный скан всех партиций.

Результат — словарь `{(student_id, course_id): attended_hours}`. Если студент не посещал курс — пары нет в словаре, используем `.get((sid, cid), 0)` при сборке.

#### Шаг 3 — Redis: профили студентов

Функция: `_get_student_profiles_redis(client, student_ids)`

Аналогично ЛР1: pipeline HGETALL для каждого студента. Возвращает `{first_name, last_name, middle_name, email, phone}`.

Для 50 студентов одной группы — 50 команд в одном TCP-пакете.

#### Шаг 4 — MongoDB: орг-иерархия

Функция: `_get_org_lookup_mongo(db)`

Аналогично ЛР1. Одним `find_one` читаем весь документ, строим словарь `{dept_id → org_info}`.

Используем `department_id` из Neo4j (первичная кафедра специальности группы) для поиска: `org_lookup.get(department_id, {})`. Это даёт `department_name`, `institute_name`, `university_name` — принадлежность студента к структурным подразделениям.

#### Шаг 5 — Сборка

```python
for cid, pg_meta in filtered_courses.items():
    neo4j_meta = neo4j_courses[cid]          # total_hours, department_name курса
    students_out = []
    for sid in student_ids:
        profile = profiles.get(sid, {})      # ФИО, контакты из Redis
        ah = attended.get((sid, cid), 0)     # 0 если не посещал
        students_out.append(StudentHours(...))
    courses_out.append(CourseHours(..., students=students_out))
```

Итерируем только по `filtered_courses` (прошедшим фильтр `is_department_discipline`). Для каждого курса — все студенты группы с их индивидуальными часами.

`department_name` у студента берётся из MongoDB (`org.get("department_name", ...)`), у курса — из Neo4j (`neo4j_meta["department_name"]`). Это разные вещи: первое — орг-принадлежность студента, второе — кто преподаёт курс. В нашей модели данных они совпадают (одна кафедра), но семантически разделены.

---

## Сводная таблица запросов по лабораторным работам

| Шаг | ЛР1 | ЛР2 | ЛР3 |
|-----|-----|-----|-----|
| **OpenSearch** | `multi_match` по term → lecture_ids | — | — |
| **Neo4j** | lecture_ids → student_ids + группа + кафедра + кол-во занятий | course_id → schedule_ids + count(students) | group_id → студенты + специальность + курсы первичной кафедры |
| **PostgreSQL** | present_count + enrollment_date (LEFT JOIN + партиция) | title + type + equipment per lecture_id | группа: curator/year; курсы: is_dept_discipline; часы: COUNT×2 (параллельно) |
| **Redis** | HGETALL student:{id} (pipeline) | — | HGETALL student:{id} (pipeline) |
| **MongoDB** | find_one → dept/inst/univ lookup | — | find_one → dept/inst/univ lookup |
| **Итог** | top-10 по attendance_percent ASC | sessions + required_capacity | courses × students с planned/attended_hours |

---

## Граничные случаи и защиты

| Ситуация | Как обработано |
|----------|----------------|
| Термин не найден в OpenSearch (ЛР1) | Возврат пустого `ReportResponse` с `students=[]`, без обращения к другим БД |
| Нет занятий в Neo4j (ЛР1, ЛР2) | Возврат `None` из сервиса → HTTP 404 в роутере |
| Группа не найдена (ЛР3) | Neo4j возвращает `[]` → `None` из сервиса → HTTP 404 |
| Студент не посещал курс (ЛР1) | LEFT JOIN в PG → `COALESCE(..., 0)` → `present_count = 0`, `attendance_percent = 0.0` |
| Студент не посещал курс (ЛР3) | `attended.get((sid, cid), 0)` → `attended_hours = 0` |
| Нет профиля в Redis | `profiles.get(sid, {})` → пустые строки для ФИО/контактов |
| Нет кафедры в MongoDB | `org_lookup.get(dept_id, {})` → пустые строки для иерархии |
| Деление на ноль (attendance%) | `if total > 0 else 0.0` |
| Neo4j DATE vs строка | `str(row["session_date"])` перед `date.fromisoformat()` |
