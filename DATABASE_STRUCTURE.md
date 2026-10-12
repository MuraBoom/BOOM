# Структура баз данных — Система учёта учебного процесса РТУ МИРЭА

## Содержание

1. [Общая архитектура](#1-общая-архитектура)
2. [PostgreSQL — транзакционное ядро](#2-postgresql--транзакционное-ядро)
3. [Redis — горячий кэш](#3-redis--горячий-кэш)
4. [MongoDB — иерархия организации](#4-mongodb--иерархия-организации)
5. [Neo4j — граф связей](#5-neo4j--граф-связей)
6. [OpenSearch — полнотекстовый поиск](#6-opensearch--полнотекстовый-поиск)
7. [Консистентность UUID между БД](#7-консистентность-uuid-между-бд)
8. [Как каждая ЛР использует хранилища](#8-как-каждая-лр-использует-хранилища)

---

## 1. Общая архитектура

Каждое хранилище выбрано под конкретную характеристику данных:

| Хранилище | Что хранит | Почему |
|-----------|-----------|--------|
| **PostgreSQL** | Все транзакционные данные: студенты, группы, расписание, посещаемость | ACID, FK, партиционирование по неделям |
| **Redis** | Профиль студента, сессии JWT-blacklist | In-memory, <1 мс, TTL |
| **MongoDB** | Иерархия Университет → Институт → Кафедра | Вложенная структура читается за 1 запрос без JOIN |
| **Neo4j** | Граф: Кафедра — Специальность — Курс — Лекция — Группа — Студент — Расписание | Обход многоуровневых связей без дорогих JOIN |
| **OpenSearch** | Полный текст лекций и учебных материалов | Инвертированный индекс, морфология русского языка |

---

## 2. PostgreSQL — транзакционное ядро

Физическая схема содержит 11 таблиц. Порядок ниже соответствует порядку создания (от родительских к дочерним).

### 2.1 `university`

Хранит единственную запись — РТУ МИРЭА.

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Глобальный идентификатор университета | FK в `institute`, ключ `university_id` в MongoDB |
| `name` | VARCHAR(255) | Название: «РТУ МИРЭА» | MongoDB-документ, вывод отчётов |
| `address` | VARCHAR(500) | Почтовый адрес | MongoDB-документ |
| `founded_year` | INT | Год основания (1947) | MongoDB-документ |

---

### 2.2 `institute`

Институт внутри университета (ИКБ, ИИТ, ИИИ).

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Идентификатор института | FK в `department`, вложенный объект в MongoDB |
| `university_id` | UUID FK → university | Ссылка на университет | JOIN при построении иерархии |
| `name` | VARCHAR(255) | Полное название | MongoDB-документ, отчёты |
| `dean` | VARCHAR(255) | ФИО декана | MongoDB-документ |

**Индекс:** `idx_institute_university_id` — ускоряет выборку институтов по университету.

---

### 2.3 `department`

Кафедра внутри института (КБ-1, КБ-3, ИТ-2 и др.).

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Идентификатор кафедры | FK в `department_specialty`, `lecture_course`; узел Neo4j `:Department`; MongoDB |
| `institute_id` | UUID FK → institute | Ссылка на институт | JOIN для построения иерархии |
| `name` | VARCHAR(255) | Полное название кафедры (напр. «КБ-3 «Разработка программных решений…»») | Отчёты, MongoDB |
| `head` | VARCHAR(255) | ФИО заведующего кафедрой | MongoDB, вывод отчётов |

**Индекс:** `idx_department_institute_id`.

---

### 2.4 `specialty`

Направление подготовки (специальность) с кодом ФГОС.

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Идентификатор специальности | FK в `department_specialty`, `lecture_course`, `student_group`; узел Neo4j `:Specialty` |
| `name` | VARCHAR(255) | Название: «Информационная безопасность» | Отчёты, Neo4j |
| `code` | VARCHAR(50) | Код ФГОС: «10.03.01» | Ключ связи между кафедрой, группой и курсом |
| `degree_level` | VARCHAR(50) | Уровень: «Бакалавриат» / «Магистратура» | Фильтрация в аналитических запросах |

---

### 2.5 `department_specialty`

Таблица-связка M:N между кафедрами и специальностями. Кафедра может вести несколько специальностей; специальность может вестись несколькими кафедрами.

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Идентификатор связи | — |
| `department_id` | UUID FK → department | Кафедра | JOIN при поиске специальностей кафедры |
| `specialty_id` | UUID FK → specialty | Специальность | JOIN при поиске кафедр специальности |
| `is_primary` | BOOLEAN | Является ли специальность профильной для кафедры | **LR3**: определяет «дисциплины кафедры»; ребро `:OFFERS_SPECIALTY {is_primary}` в Neo4j |

**Индексы:** `idx_dept_spec_department_id`, `idx_dept_spec_specialty_id`.
**Ограничение:** `UNIQUE(department_id, specialty_id)` — нельзя добавить одну и ту же пару дважды.

---

### 2.6 `lecture_course`

Учебный курс, привязанный к специальности и кафедре, читающийся в конкретном семестре.

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Идентификатор курса | FK в `lecture`; узел Neo4j `:LectureCourse` |
| `specialty_id` | UUID FK → specialty | Специальность, для которой читается курс | Связь Neo4j `:BELONGS_TO_SPECIALTY` |
| `department_id` | UUID FK → department | Кафедра, ведущая курс | Связь Neo4j `:TAUGHT_BY_DEPARTMENT` |
| `name` | VARCHAR(255) | Название: «Проектирование архитектуры ПО» | Вывод во всех отчётах |
| `semester` | INT (1–12) | Номер семестра | **LR2**: фильтр по семестру и году |
| `academic_year` | INT | Учебный год (2024) | **LR2**: фильтр по семестру и году |
| `total_hours` | INT | Плановое число академических часов | **LR3**: «необходимый объём запланированных часов» |
| `is_department_discipline` | BOOLEAN | Флаг: является ли курс профильной дисциплиной кафедры | **LR3**: `WHERE is_department_discipline = TRUE` |

**Индексы:** `idx_lecture_course_specialty_id`, `idx_lecture_course_department_id`, `idx_lecture_course_semester_year`.

---

### 2.7 `lecture`

Отдельное занятие (лекция, практика или лаб) внутри курса.

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Идентификатор занятия | FK в `lecture_material`, `schedule`; узел Neo4j `:Lecture`; документ OpenSearch |
| `course_id` | UUID FK → lecture_course | Курс, к которому относится занятие | JOIN при построении программы курса |
| `title` | VARCHAR(500) | Тема: «Нормализация БД» | OpenSearch: поиск по `title^3`; **LR1**: фильтр по ключевому слову |
| `annotation` | TEXT | Краткое описание содержания | OpenSearch: поиск по `annotation^2` |
| `lecture_type` | VARCHAR(20) | Тип: `lecture` / `practice` / `lab` | Фильтрация; Neo4j свойство узла |
| `order_number` | INT | Порядковый номер в рамках курса | Сортировка расписания |
| `equipment_requirements` | TEXT | Требования к оборудованию: «Проектор, ноутбуки» | **LR2**: выборка необходимого оборудования |

**Индексы:** `idx_lecture_course_id`, `idx_lecture_type`.

---

### 2.8 `lecture_material`

Материалы занятия (полный текст слайдов, PDF и т.п.).

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Идентификатор материала | — |
| `lecture_id` | UUID FK → lecture | Занятие, к которому относится материал | JOIN; источник `materials_text` для OpenSearch |
| `content_type` | VARCHAR(50) | MIME-тип: `text/markdown`, `application/pdf` | Выбор способа рендеринга |
| `content_text` | TEXT | Полный текст материала | **OpenSearch**: индексируется как `materials_text`; **LR1**: поиск по тексту |
| `file_url` | VARCHAR(1000) | URL прикреплённого файла (если есть) | Скачивание материалов |
| `created_at` | TIMESTAMP | Дата добавления материала | Сортировка, аудит |

**Индекс:** `idx_lecture_material_lecture_id`.

---

### 2.9 `student_group`

Учебная группа (БСБО-01-23, БИВТ-02-23 и т.д.).

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | Идентификатор группы | FK в `student`, `schedule`; узел Neo4j `:StudentGroup` |
| `specialty_id` | UUID FK → specialty | Специальность, по которой учится группа | JOIN для поиска курсов группы; связь Neo4j `:STUDIES_SPECIALTY` |
| `name` | VARCHAR(50) | Название: «БСБО-01-23» | Вывод во всех отчётах; ключ Redis `group_name` |
| `enrollment_year` | INT | Год поступления (2022 / 2023) | Фильтрация по году обучения |
| `curator` | VARCHAR(255) | ФИО куратора группы | Вывод в отчётах; узел Neo4j |

**Индекс:** `idx_student_group_specialty_id`.

---

### 2.10 `student`

Студент — центральная сущность всей системы.

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID PK | **Главный идентификатор** студента | FK в `attendance`; ключ Redis `student:{id}`; узел Neo4j `:Student`; поиск по всем БД |
| `group_id` | UUID FK → student_group | Группа студента | JOIN для определения курсов; связь Neo4j `:MEMBER_OF` |
| `first_name` | VARCHAR(100) | Имя | Redis hash, отчёты |
| `last_name` | VARCHAR(100) | Фамилия | Redis hash, отчёты |
| `middle_name` | VARCHAR(100) | Отчество | Redis hash, полное ФИО |
| `email` | VARCHAR(255) | Email студента | Redis hash, контакт |
| `phone` | VARCHAR(50) | Телефон | Redis hash |
| `enrollment_date` | DATE | Дата зачисления | Фильтрация по году |

**Индекс:** `idx_student_group_id`.

---

### 2.11 `schedule` *(партиционирована)*

Запланированное занятие для конкретной группы в конкретную неделю.

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID | Идентификатор занятия в расписании | FK в `attendance`; узел Neo4j `:Schedule` |
| `lecture_id` | UUID FK → lecture | Какое занятие проводится | JOIN для получения темы, оборудования |
| `group_id` | UUID FK → student_group | Для какой группы | JOIN для подсчёта слушателей (**LR2**) |
| `week_start_date` | DATE **partition key** | Дата начала недели: `2024-09-02`, `2024-09-09`... | Ключ партиции; фильтр по периоду (**LR1**, **LR3**) |
| `start_time` | TIME | Время начала: `09:00` | Отчёт о расписании |
| `end_time` | TIME | Время окончания: `10:30` | Отчёт о расписании |
| `classroom` | VARCHAR | Аудитория: «А-205», «Лаборатория сетей» | **LR2**: привязка аудитории к занятию |

**PRIMARY KEY:** `(id, week_start_date)` — оба поля обязательны для партиционированных таблиц.

**Партиции:**

| Партиция | Диапазон | Неделя учёбы |
|----------|----------|--------------|
| `schedule_2024_w36` | `[2024-09-02, 2024-09-09)` | 1-я неделя семестра |
| `schedule_2024_w37` | `[2024-09-09, 2024-09-16)` | 2-я неделя |
| `schedule_2024_w38` | `[2024-09-16, 2024-09-23)` | 3-я неделя |
| `schedule_2024_w39` | `[2024-09-23, 2024-09-30)` | 4-я неделя |
| `schedule_default` | все остальные даты | будущие семестры |

**Индексы:** `idx_schedule_lecture_id`, `idx_schedule_group_id`, `idx_schedule_date`, `idx_schedule_group_date`.

---

### 2.12 `attendance` *(партиционирована)*

Факт посещения студентом конкретного занятия. Генерируется случайно при заполнении (≈70% посещаемость).

| Поле | Тип | Описание | Используется в |
|------|-----|----------|----------------|
| `id` | UUID | Идентификатор записи посещения | — |
| `schedule_id` | UUID FK → schedule | Какое занятие посетил | JOIN для определения лекции, недели |
| `student_id` | UUID FK → student | Кто посетил | JOIN для агрегации по студенту; **LR1**: подсчёт % посещаемости |
| `week_start_date` | DATE **partition key** | Дублирует `schedule.week_start_date` — обязательно для партиции | Ключ секции; фильтр периода (**LR1**, **LR3**) |
| `marked_at` | TIMESTAMP | Когда зафиксировано посещение | Аудит, сортировка |

**PRIMARY KEY:** `(id, week_start_date)`.
**UNIQUE:** `(schedule_id, student_id, week_start_date)` — студент не может быть отмечен дважды на одном занятии за одну неделю.

**Партиции:** аналогичны `schedule` (w36–w39 + default).

**Индексы:** `idx_attendance_schedule_id`, `idx_attendance_student_id`, `idx_attendance_date`, `idx_attendance_student_date`.

---

### Полная ER-схема (текстовая)

```
university (1)
    └── institute (N)
            └── department (N)
                    ├── department_specialty (M:N) ── specialty
                    └── lecture_course ────────────── specialty
                            └── lecture
                                    ├── lecture_material
                                    └── schedule ──── student_group ── specialty
                                            └── attendance ─────────── student
                                                                            └── student_group
```

---

## 3. Redis — горячий кэш

Redis хранит только **горячие данные**, запрашиваемые при каждом HTTP-запросе.

### 3.1 Ключ `student:{student_id}`

**Тип:** Hash

Профиль студента загружается один раз из PostgreSQL и кэшируется с TTL 2 часа.

| Поле хэша | Значение | Используется в |
|-----------|---------|----------------|
| `first_name` | Имя | **LR1**: обогащение результата именем студента |
| `last_name` | Фамилия | **LR1**: обогащение результата |
| `middle_name` | Отчество | Полное ФИО в отчётах |
| `group_name` | Название группы (БСБО-01-23) | **LR3**: быстрый доступ к названию группы |
| `email` | Email | Контактная информация в отчётах |
| `phone` | Телефон | Контактная информация |

**TTL:** 7200 секунд (2 часа). По истечении — повторная загрузка из PostgreSQL.

**Зачем Redis, а не PostgreSQL:** Redis отвечает за <1 мс. LR1 запрашивает профили 10 студентов — это 10 `HGETALL` из памяти вместо 10 JOIN'ов к диску.

### 3.2 Ключ `session:{user_id}`

**Тип:** String (JSON)

JWT-blacklist и данные сессии после аутентификации.

```
SET session:user-uuid "{\"user_id\":\"...\",\"role\":\"student\"}" EX 86400
```

**TTL:** 86400 секунд (24 часа).

---

## 4. MongoDB — иерархия организации

**База данных:** `university_db`
**Коллекция:** `organization_structure`

Хранит **один документ** на университет с полностью вложенной структурой.

### Структура документа

```json
{
  "_id": ObjectId("..."),
  "university_id": "uuid-из-PostgreSQL",
  "name": "РТУ МИРЭА",
  "address": "119454, г. Москва, пр-т Вернадского, 78",
  "founded_year": 1947,
  "institutes": [
    {
      "institute_id": "uuid-из-PostgreSQL",
      "name": "Институт кибербезопасности и цифровых технологий",
      "dean": "Захаров Андрей Николаевич",
      "departments": [
        {
          "department_id": "uuid-из-PostgreSQL",
          "name": "КБ-3 «Разработка программных решений и системное программирование»",
          "head": "Волович Михаил Евгеньевич"
        }
      ]
    }
  ],
  "updated_at": ISODate("2024-09-01T00:00:00Z")
}
```

### Поля верхнего уровня

| Поле | Описание |
|------|----------|
| `university_id` | UUID из PostgreSQL — обеспечивает синхронизацию с PG |
| `name` | Название университета |
| `address` | Адрес |
| `founded_year` | Год основания |
| `institutes` | Массив институтов (см. ниже) |
| `updated_at` | Метка для инвалидации кэша |

### Поля объекта `institutes[]`

| Поле | Описание |
|------|----------|
| `institute_id` | UUID из PostgreSQL (таблица `institute`) |
| `name` | Название института |
| `dean` | ФИО декана |
| `departments` | Вложенный массив кафедр |

### Поля объекта `departments[]`

| Поле | Описание |
|------|----------|
| `department_id` | UUID из PostgreSQL (таблица `department`) |
| `name` | Название кафедры |
| `head` | ФИО заведующего |

**Зачем MongoDB, а не PostgreSQL:** вместо `university JOIN institute JOIN department` (3 JOIN'а) — один `db.organization_structure.findOne({university_id: "..."})`. Данные меняются редко, вложенная структура читается целиком.

---

## 5. Neo4j — граф связей

### 5.1 Узлы (Nodes)

| Узел | Свойства | Откуда UUID |
|------|----------|-------------|
| `:Department` | `id`, `name`, `head` | `department.id` из PG |
| `:Specialty` | `id`, `name`, `code`, `degree_level` | `specialty.id` из PG |
| `:LectureCourse` | `id`, `name`, `semester`, `academic_year`, `total_hours`, `is_department_discipline` | `lecture_course.id` из PG |
| `:Lecture` | `id`, `title`, `annotation`, `lecture_type`, `order_number`, `equipment_requirements` | `lecture.id` из PG |
| `:StudentGroup` | `id`, `name`, `enrollment_year`, `curator` | `student_group.id` из PG |
| `:Student` | `id`, `first_name`, `last_name`, `email`, `phone` | `student.id` из PG |
| `:Schedule` | `id`, `scheduled_date`, `start_time`, `end_time`, `classroom` | `schedule.id` из PG |

### 5.2 Связи (Relationships)

| Связь | Направление | Свойства | Описание |
|-------|-------------|----------|----------|
| `:OFFERS_SPECIALTY` | Department → Specialty | `is_primary: bool` | Кафедра ведёт специальность; `is_primary=true` — профильная |
| `:BELONGS_TO_SPECIALTY` | LectureCourse → Specialty | — | Курс читается для данной специальности |
| `:TAUGHT_BY_DEPARTMENT` | LectureCourse → Department | — | Курс ведёт данная кафедра |
| `:CONTAINS_LECTURE` | LectureCourse → Lecture | — | Занятие входит в программу курса |
| `:STUDIES_SPECIALTY` | StudentGroup → Specialty | — | Группа обучается по специальности |
| `:MEMBER_OF` | Student → StudentGroup | — | Студент входит в группу |
| `:FOR_LECTURE` | Schedule → Lecture | — | Занятие в расписании ссылается на лекцию |
| `:FOR_GROUP` | Schedule → StudentGroup | — | Занятие в расписании для группы |

### 5.3 Примеры Cypher-запросов

**Все лекции, которые должен посетить студент:**
```cypher
MATCH (s:Student {id: $student_id})
  -[:MEMBER_OF]->(g:StudentGroup)
  -[:STUDIES_SPECIALTY]->(sp:Specialty)
  <-[:BELONGS_TO_SPECIALTY]-(c:LectureCourse)
  -[:CONTAINS_LECTURE]->(l:Lecture)
RETURN l.title, l.lecture_type, c.name
```

**Количество студентов на каждом занятии курса (для LR2):**
```cypher
MATCH (c:LectureCourse {id: $course_id})
  -[:CONTAINS_LECTURE]->(l:Lecture)
  <-[:FOR_LECTURE]-(sch:Schedule)
  -[:FOR_GROUP]->(g:StudentGroup)
  <-[:MEMBER_OF]-(s:Student)
RETURN l.title, l.equipment_requirements, count(s) AS student_count
```

**Профильные курсы кафедры для группы (для LR3):**
```cypher
MATCH (g:StudentGroup {id: $group_id})
  -[:STUDIES_SPECIALTY]->(sp:Specialty)
  <-[:BELONGS_TO_SPECIALTY]-(c:LectureCourse {is_department_discipline: true})
  -[:CONTAINS_LECTURE]->(l:Lecture)
RETURN g.name, c.name, c.total_hours, collect(l.id) AS lecture_ids
```

**Зачем Neo4j, а не PostgreSQL:** запрос «все лекции студента» требует в PG цепочки `student → student_group → specialty → lecture_course → lecture` (4 JOIN'а). В Neo4j это прямой обход паттерна `(s)-[:MEMBER_OF]->(g)-[:STUDIES_SPECIALTY]->...` без JOIN.

---

## 6. OpenSearch — полнотекстовый поиск

**Индекс:** `lectures`

### 6.1 Маппинг полей

| Поле | Тип | Анализатор | Описание |
|------|-----|-----------|----------|
| `lecture_id` | `keyword` | — | UUID лекции из PostgreSQL; точное совпадение |
| `course_id` | `keyword` | — | UUID курса; фильтрация по курсу |
| `title` | `text` | `russian` | Тема занятия; вес `^3` при поиске |
| `annotation` | `text` | `russian` | Краткое описание; вес `^2` при поиске |
| `materials_text` | `text` | `russian` | Полный текст материалов; основной корпус для поиска |
| `lecture_type` | `keyword` | — | `lecture` / `practice` / `lab`; фильтрация |

### 6.2 Русский анализатор

```
tokenizer: standard → lowercase → russian_stop → russian_stemmer
```

| Шаг | Что делает |
|-----|-----------|
| `standard tokenizer` | Разбивает на токены по пробелам и пунктуации |
| `lowercase` | Приводит к нижнему регистру |
| `russian_stop` | Удаляет стоп-слова: «и», «в», «на», «по», «для», … |
| `russian_stemmer` | Приводит к корневой форме: «нормализация» → «нормализац», «нормализации» → «нормализац» |

**Результат:** запрос `"нормализация"` находит документы со словами «нормализация», «нормализации», «нормализовать», «нормализованный».

### 6.3 Пример поискового запроса (LR1)

```json
{
  "query": {
    "multi_match": {
      "query": "микросервисы декомпозиция",
      "fields": ["title^3", "annotation^2", "materials_text"],
      "type": "best_fields",
      "operator": "or"
    }
  }
}
```

**Зачем OpenSearch, а не PostgreSQL LIKE:** `LIKE '%слово%'` — полный скан таблицы, O(n). OpenSearch — инвертированный индекс, O(1) плюс ранжирование по релевантности и морфология.

---

## 7. Консистентность UUID между БД

UUID генерируется **один раз в Python** (seeder) и записывается во все базы данных. Это обеспечивает возможность перекрёстных запросов.

```
Сущность         PostgreSQL          Redis               Neo4j               MongoDB
─────────────────────────────────────────────────────────────────────────────────────
Студент          student.id          student:{id}        :Student {id}       —
Группа           student_group.id    (group_name)        :StudentGroup {id}  —
Кафедра          department.id       —                   :Department {id}    department_id
Институт         institute.id        —                   —                   institute_id
Университет      university.id       —                   —                   university_id
Курс             lecture_course.id   —                   :LectureCourse {id} —
Лекция           lecture.id          —                   :Lecture {id}       lecture_id (OpenSearch)
Расписание       schedule.id         —                   :Schedule {id}      —
```

**Пример сквозного запроса (LR1):**
```
OpenSearch → lecture_id → Neo4j (найти student_id) → PostgreSQL (посчитать %) → Redis (получить имя)
```
На каждом шаге UUID служит связующим ключом.

---

## 8. Как каждая ЛР использует хранилища

### ЛР №1 — Топ-10 прогульщиков по теме

**Цель:** найти 10 студентов с наименьшим % посещаемости по лекциям, содержащим заданный термин.

| Шаг | Хранилище | Операция |
|-----|-----------|---------|
| 1 | **OpenSearch** | `multi_match` по `title`/`annotation`/`materials_text` → список `lecture_id` |
| 2 | **Neo4j** | По `lecture_id` через `:FOR_LECTURE←Schedule→:FOR_GROUP←:MEMBER_OF←Student` → список `student_id` |
| 3 | **PostgreSQL** | `SELECT student_id, COUNT(*) / total * 100 AS pct FROM attendance WHERE schedule_id IN (...)` GROUP BY, ORDER BY pct ASC LIMIT 10 |
| 4 | **Redis** | `HGETALL student:{id}` × 10 — обогатить ФИО, email, группой |

### ЛР №2 — Вместимость аудитории для курса

**Цель:** для курса заданного семестра и года вывести: лекция, количество слушателей, требования к оборудованию.

| Шаг | Хранилище | Операция |
|-----|-----------|---------|
| 1 | **Neo4j** | По `course_id`, семестру и году через `:CONTAINS_LECTURE←LectureCourse`, `:FOR_LECTURE←Schedule→:FOR_GROUP←:MEMBER_OF←Student` → `count(Student)` на каждое занятие |
| 2 | **PostgreSQL** | `SELECT l.equipment_requirements FROM lecture WHERE id IN (...)` |

### ЛР №3 — Аудит профильных часов по группе

**Цель:** по группе вывести плановые и посещённые часы по профильным дисциплинам кафедры.

| Шаг | Хранилище | Операция |
|-----|-----------|---------|
| 1 | **Neo4j** | Найти `LectureCourse {is_department_discipline: true}` для группы через специальность → список `lecture_id` и `total_hours` |
| 2 | **PostgreSQL** | `SELECT student_id, COUNT(*) * 2 AS visited_hours FROM attendance WHERE student_id IN (...) AND schedule_id IN (...)` — 1 лекция = 2 ч. |
| 3 | **Redis** | `HGETALL student:{id}` — профили студентов группы для вывода ФИО |
