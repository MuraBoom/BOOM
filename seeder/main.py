import asyncio
import os
import random
import uuid
from datetime import date, datetime, timedelta
from datetime import UTC as dUTC
from datetime import time as dtime

import asyncpg
import redis.asyncio as aioredis
from faker import Faker
from motor.motor_asyncio import AsyncIOMotorClient
from neo4j import AsyncGraphDatabase
from opensearchpy import AsyncOpenSearch, helpers

from constants import (
    CLASSROOMS,
    COURSES,
    COURSE_LECTURES,
    DEPARTMENTS,
    DEPT_SPECIALTY_MAP,
    INSTITUTES,
    LESSON_TIMES,
    SPECIALTIES,
    STUDENT_COUNT_PER_GROUP,
    STUDENT_GROUPS,
    UNIVERSITIES,
    WEEK_DATES,
)

fake = Faker("ru_RU")
random.seed(42)


class UniversitySeeder:
    def __init__(self):
        pg_host = os.getenv("POSTGRES_HOST", "localhost")
        pg_port = os.getenv("POSTGRES_PORT", "5432")
        pg_user = os.getenv("POSTGRES_USER", "postgres")
        pg_pass = os.getenv("POSTGRES_PASSWORD", "postgres")
        pg_db = os.getenv("POSTGRES_DB", "postgres")

        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = os.getenv("REDIS_PORT", "6379")

        mongo_host = os.getenv("MONGO_HOST", "localhost")
        mongo_user = os.getenv("MONGO_INITDB_ROOT_USERNAME", "admin")
        mongo_pass = os.getenv("MONGO_INITDB_ROOT_PASSWORD", "admin")

        neo4j_host = os.getenv("NEO4J_HOST", "localhost")
        neo4j_auth_raw = os.getenv("NEO4J_AUTH", "none")

        os_host = os.getenv("OPENSEARCH_HOST", "localhost")
        os_port = os.getenv("OPENSEARCH_PORT", "9200")
        os_pass = os.getenv("OPENSEARCH_INITIAL_ADMIN_PASSWORD", "Kirill1234!Kirill")

        self.pg_dsn = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"
        self.redis_url = f"redis://{redis_host}:{redis_port}"
        self.mongo_url = f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:27017"
        self.neo4j_uri = f"bolt://{neo4j_host}:7687"

        if neo4j_auth_raw == "none":
            self.neo4j_auth = None
        else:
            neo4j_user, neo4j_pw = neo4j_auth_raw.split("/", 1)
            self.neo4j_auth = (neo4j_user, neo4j_pw)

        self.os_url = f"http://{os_host}:{os_port}"
        self.os_auth = ("admin", os_pass)

    async def init_connections(self):
        self.pg = await asyncpg.connect(self.pg_dsn)
        self.redis = aioredis.from_url(self.redis_url, decode_responses=True)
        self.mongo_client = AsyncIOMotorClient(self.mongo_url)
        self.mongo_db = self.mongo_client["university_db"]
        self.neo4j = AsyncGraphDatabase.driver(self.neo4j_uri, auth=self.neo4j_auth)
        self.os = AsyncOpenSearch(
            hosts=[self.os_url],
            http_auth=self.os_auth,
            use_ssl=True,
            verify_certs=False,
            ssl_show_warn=False,
        )

    async def close_connections(self):
        await self.pg.close()
        await self.redis.aclose()
        self.mongo_client.close()
        await self.neo4j.close()
        await self.os.close()

    async def clean_all(self):
        print("--- Очистка всех баз данных ---")
        await self.pg.execute("""
            DROP TABLE IF EXISTS attendance CASCADE;
            DROP TABLE IF EXISTS schedule CASCADE;
            DROP TABLE IF EXISTS student CASCADE;
            DROP TABLE IF EXISTS student_group CASCADE;
            DROP TABLE IF EXISTS lecture_material CASCADE;
            DROP TABLE IF EXISTS lecture CASCADE;
            DROP TABLE IF EXISTS lecture_course CASCADE;
            DROP TABLE IF EXISTS department_specialty CASCADE;
            DROP TABLE IF EXISTS specialty CASCADE;
            DROP TABLE IF EXISTS department CASCADE;
            DROP TABLE IF EXISTS institute CASCADE;
            DROP TABLE IF EXISTS university CASCADE;
        """)
        await self.redis.flushdb()
        await self.mongo_client.drop_database("university_db")
        async with self.neo4j.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")
        if await self.os.indices.exists(index="lectures"):
            await self.os.indices.delete(index="lectures")

    async def create_schema_pg(self):
        print("--- Создание схемы PostgreSQL ---")
        await self.pg.execute("""
            CREATE EXTENSION IF NOT EXISTS pgcrypto;

            CREATE TABLE IF NOT EXISTS university (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL,
                address VARCHAR(500),
                founded_year INT
            );

            CREATE TABLE IF NOT EXISTS institute (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                university_id UUID NOT NULL REFERENCES university(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                dean VARCHAR(255)
            );
            CREATE INDEX IF NOT EXISTS idx_institute_university_id ON institute(university_id);

            CREATE TABLE IF NOT EXISTS department (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                institute_id UUID NOT NULL REFERENCES institute(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                head VARCHAR(255)
            );
            CREATE INDEX IF NOT EXISTS idx_department_institute_id ON department(institute_id);

            CREATE TABLE IF NOT EXISTS specialty (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL,
                code VARCHAR(50) NOT NULL,
                degree_level VARCHAR(50)
            );

            CREATE TABLE IF NOT EXISTS department_specialty (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                department_id UUID NOT NULL REFERENCES department(id) ON DELETE CASCADE,
                specialty_id UUID NOT NULL REFERENCES specialty(id) ON DELETE CASCADE,
                is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                UNIQUE(department_id, specialty_id)
            );
            CREATE INDEX IF NOT EXISTS idx_dept_spec_department_id ON department_specialty(department_id);
            CREATE INDEX IF NOT EXISTS idx_dept_spec_specialty_id ON department_specialty(specialty_id);

            CREATE TABLE IF NOT EXISTS lecture_course (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                specialty_id UUID NOT NULL REFERENCES specialty(id) ON DELETE CASCADE,
                department_id UUID NOT NULL REFERENCES department(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                semester INT NOT NULL CHECK (semester BETWEEN 1 AND 12),
                academic_year INT NOT NULL,
                total_hours INT NOT NULL DEFAULT 0,
                is_department_discipline BOOLEAN NOT NULL DEFAULT FALSE
            );
            CREATE INDEX IF NOT EXISTS idx_lecture_course_specialty_id ON lecture_course(specialty_id);
            CREATE INDEX IF NOT EXISTS idx_lecture_course_department_id ON lecture_course(department_id);
            CREATE INDEX IF NOT EXISTS idx_lecture_course_semester_year ON lecture_course(semester, academic_year);

            CREATE TABLE IF NOT EXISTS lecture (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                course_id UUID NOT NULL REFERENCES lecture_course(id) ON DELETE CASCADE,
                title VARCHAR(500) NOT NULL,
                annotation TEXT,
                lecture_type VARCHAR(20) NOT NULL CHECK (lecture_type IN ('lecture', 'practice', 'lab')),
                order_number INT NOT NULL,
                equipment_requirements TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_lecture_course_id ON lecture(course_id);
            CREATE INDEX IF NOT EXISTS idx_lecture_type ON lecture(lecture_type);

            CREATE TABLE IF NOT EXISTS lecture_material (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                lecture_id UUID NOT NULL REFERENCES lecture(id) ON DELETE CASCADE,
                content_type VARCHAR(50) NOT NULL,
                content_text TEXT,
                file_url VARCHAR(1000),
                created_at TIMESTAMP DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_lecture_material_lecture_id ON lecture_material(lecture_id);

            CREATE TABLE IF NOT EXISTS student_group (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                specialty_id UUID NOT NULL REFERENCES specialty(id) ON DELETE CASCADE,
                name VARCHAR(50) NOT NULL,
                enrollment_year INT NOT NULL,
                curator VARCHAR(255)
            );
            CREATE INDEX IF NOT EXISTS idx_student_group_specialty_id ON student_group(specialty_id);

            CREATE TABLE IF NOT EXISTS student (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                group_id UUID NOT NULL REFERENCES student_group(id) ON DELETE CASCADE,
                first_name VARCHAR(100) NOT NULL,
                last_name VARCHAR(100) NOT NULL,
                middle_name VARCHAR(100),
                email VARCHAR(255),
                phone VARCHAR(50),
                enrollment_date DATE NOT NULL DEFAULT CURRENT_DATE
            );
            CREATE INDEX IF NOT EXISTS idx_student_group_id ON student(group_id);

            CREATE TABLE IF NOT EXISTS schedule (
                id UUID NOT NULL DEFAULT gen_random_uuid(),
                lecture_id UUID NOT NULL,
                group_id UUID NOT NULL,
                week_start_date DATE NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                classroom VARCHAR,
                PRIMARY KEY (id, week_start_date)
            ) PARTITION BY RANGE (week_start_date);

            CREATE TABLE IF NOT EXISTS schedule_2024_w36 PARTITION OF schedule
                FOR VALUES FROM ('2024-09-02') TO ('2024-09-09');
            CREATE TABLE IF NOT EXISTS schedule_2024_w37 PARTITION OF schedule
                FOR VALUES FROM ('2024-09-09') TO ('2024-09-16');
            CREATE TABLE IF NOT EXISTS schedule_2024_w38 PARTITION OF schedule
                FOR VALUES FROM ('2024-09-16') TO ('2024-09-23');
            CREATE TABLE IF NOT EXISTS schedule_2024_w39 PARTITION OF schedule
                FOR VALUES FROM ('2024-09-23') TO ('2024-09-30');
            CREATE TABLE IF NOT EXISTS schedule_default PARTITION OF schedule DEFAULT;

            CREATE INDEX IF NOT EXISTS idx_schedule_lecture_id ON schedule(lecture_id);
            CREATE INDEX IF NOT EXISTS idx_schedule_group_id ON schedule(group_id);
            CREATE INDEX IF NOT EXISTS idx_schedule_date ON schedule(week_start_date);
            CREATE INDEX IF NOT EXISTS idx_schedule_group_date ON schedule(group_id, week_start_date);

            CREATE TABLE IF NOT EXISTS attendance (
                id UUID NOT NULL DEFAULT gen_random_uuid(),
                schedule_id UUID NOT NULL,
                student_id UUID NOT NULL,
                week_start_date DATE NOT NULL,
                marked_at TIMESTAMP DEFAULT now(),
                PRIMARY KEY (id, week_start_date),
                UNIQUE (schedule_id, student_id, week_start_date)
            ) PARTITION BY RANGE (week_start_date);

            CREATE TABLE IF NOT EXISTS attendance_2024_w36 PARTITION OF attendance
                FOR VALUES FROM ('2024-09-02') TO ('2024-09-09');
            CREATE TABLE IF NOT EXISTS attendance_2024_w37 PARTITION OF attendance
                FOR VALUES FROM ('2024-09-09') TO ('2024-09-16');
            CREATE TABLE IF NOT EXISTS attendance_2024_w38 PARTITION OF attendance
                FOR VALUES FROM ('2024-09-16') TO ('2024-09-23');
            CREATE TABLE IF NOT EXISTS attendance_2024_w39 PARTITION OF attendance
                FOR VALUES FROM ('2024-09-23') TO ('2024-09-30');
            CREATE TABLE IF NOT EXISTS attendance_default PARTITION OF attendance DEFAULT;

            CREATE INDEX IF NOT EXISTS idx_attendance_schedule_id ON attendance(schedule_id);
            CREATE INDEX IF NOT EXISTS idx_attendance_student_id ON attendance(student_id);
            CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(week_start_date);
            CREATE INDEX IF NOT EXISTS idx_attendance_student_date ON attendance(student_id, week_start_date);
        """)

    async def create_opensearch_index(self):
        print("--- Создание индекса OpenSearch ---")
        await self.os.indices.create(
            index="lectures",
            body={
                "settings": {
                    "analysis": {
                        "analyzer": {
                            "russian": {
                                "type": "custom",
                                "tokenizer": "standard",
                                "filter": [
                                    "lowercase",
                                    "russian_stop",
                                    "russian_stemmer",
                                ],
                            }
                        },
                        "filter": {
                            "russian_stop": {"type": "stop", "stopwords": "_russian_"},
                            "russian_stemmer": {
                                "type": "stemmer",
                                "language": "russian",
                            },
                        },
                    }
                },
                "mappings": {
                    "properties": {
                        "lecture_id": {"type": "keyword"},
                        "course_id": {"type": "keyword"},
                        "title": {"type": "text", "analyzer": "russian"},
                        "annotation": {"type": "text", "analyzer": "russian"},
                        "materials_text": {"type": "text", "analyzer": "russian"},
                        "lecture_type": {"type": "keyword"},
                    }
                },
            },
        )

    async def seed_university_structure(self):
        print("--- Заполнение университет / институты / кафедры / специальности ---")

        self.university_id = uuid.uuid4()
        u = UNIVERSITIES[0]
        await self.pg.execute(
            "INSERT INTO university(id, name, address, founded_year) VALUES ($1,$2,$3,$4)",
            self.university_id,
            u["name"],
            u["address"],
            u["founded_year"],
        )

        self.institute_ids = {}
        self.dept_ids = {}
        self.dept_name_part_to_id = {}

        for inst_info in INSTITUTES:
            i_id = uuid.uuid4()
            self.institute_ids[inst_info["name"]] = i_id
            await self.pg.execute(
                "INSERT INTO institute(id, university_id, name, dean) VALUES ($1,$2,$3,$4)",
                i_id,
                self.university_id,
                inst_info["name"],
                inst_info["dean"],
            )
            for dept_info in DEPARTMENTS[inst_info["name"]]:
                d_id = uuid.uuid4()
                self.dept_ids[dept_info["name"]] = d_id
                await self.pg.execute(
                    "INSERT INTO department(id, institute_id, name, head) VALUES ($1,$2,$3,$4)",
                    d_id,
                    i_id,
                    dept_info["name"],
                    dept_info["head"],
                )
                short = dept_info["name"].split()[0]
                if short not in self.dept_name_part_to_id:
                    self.dept_name_part_to_id[short] = d_id

        self.spec_code_to_id = {}
        self.spec_code_to_name = {}
        for sp in SPECIALTIES:
            s_id = uuid.uuid4()
            self.spec_code_to_id[sp["code"]] = s_id
            self.spec_code_to_name[sp["code"]] = sp["name"]
            await self.pg.execute(
                "INSERT INTO specialty(id, name, code, degree_level) VALUES ($1,$2,$3,$4)",
                s_id,
                sp["name"],
                sp["code"],
                sp["degree_level"],
            )

        self.dept_spec_is_primary = {}
        for dept_part, spec_code, is_primary in DEPT_SPECIALTY_MAP:
            dept_id = self.dept_name_part_to_id.get(dept_part)
            spec_id = self.spec_code_to_id.get(spec_code)
            if not dept_id or not spec_id:
                continue
            link_id = uuid.uuid4()
            await self.pg.execute(
                "INSERT INTO department_specialty(id, department_id, specialty_id, is_primary) VALUES ($1,$2,$3,$4)"
                " ON CONFLICT (department_id, specialty_id) DO NOTHING",
                link_id,
                dept_id,
                spec_id,
                is_primary,
            )
            self.dept_spec_is_primary[(dept_id, spec_id)] = is_primary

    async def seed_groups_and_students(self):
        print(
            f"--- Заполнение групп и студентов (~{len(STUDENT_GROUPS) * STUDENT_COUNT_PER_GROUP}) ---"
        )

        self.group_ids = {}
        self.group_students = {}
        self.students = {}

        for grp in STUDENT_GROUPS:
            spec_id = self.spec_code_to_id.get(grp["specialty_code"])
            if not spec_id:
                continue
            g_id = uuid.uuid4()
            self.group_ids[grp["name"]] = g_id
            self.group_students[g_id] = []
            await self.pg.execute(
                "INSERT INTO student_group(id, specialty_id, name, enrollment_year, curator) VALUES ($1,$2,$3,$4,$5)",
                g_id,
                spec_id,
                grp["name"],
                grp["enrollment_year"],
                grp["curator"],
            )

        student_records = []
        for grp in STUDENT_GROUPS:
            g_id = self.group_ids.get(grp["name"])
            if not g_id:
                continue
            for _ in range(STUDENT_COUNT_PER_GROUP):
                s_id = uuid.uuid4()
                last = fake.last_name()
                first = fake.first_name()
                middle = fake.middle_name()
                email = f"s{random.randint(1000000, 9999999)}@mirea.ru"
                phone = fake.phone_number()
                enroll_date = date(grp["enrollment_year"], 9, 1)

                self.students[s_id] = {
                    "first_name": first,
                    "last_name": last,
                    "middle_name": middle,
                    "email": email,
                    "phone": phone,
                    "group_id": g_id,
                    "group_name": grp["name"],
                }
                self.group_students[g_id].append(s_id)
                student_records.append(
                    (s_id, g_id, first, last, middle, email, phone, enroll_date)
                )

        await self.pg.copy_records_to_table(
            "student",
            records=student_records,
            columns=[
                "id",
                "group_id",
                "first_name",
                "last_name",
                "middle_name",
                "email",
                "phone",
                "enrollment_date",
            ],
        )
        print(f"    Создано студентов: {len(student_records)}")

    async def seed_courses_and_lectures(self):
        print("--- Заполнение курсов, лекций и материалов → OpenSearch ---")

        self.course_ids = {}
        self.lecture_ids = {}
        self.spec_courses = {}

        os_actions = []
        lm_records = []

        for (
            dept_part,
            spec_code,
            semester,
            academic_year,
            total_hours,
            is_dept_disc,
            course_name,
        ) in COURSES:
            dept_id = self.dept_name_part_to_id.get(dept_part)
            spec_id = self.spec_code_to_id.get(spec_code)
            if not dept_id or not spec_id:
                print(
                    f"    WARN: не найдена кафедра {dept_part} или специальность {spec_code}"
                )
                continue

            c_id = uuid.uuid4()
            self.course_ids[course_name] = c_id
            self.spec_courses.setdefault(spec_code, []).append(c_id)

            await self.pg.execute(
                "INSERT INTO lecture_course(id, specialty_id, department_id, name, semester, academic_year, total_hours, is_department_discipline)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                c_id,
                spec_id,
                dept_id,
                course_name,
                semester,
                academic_year,
                total_hours,
                is_dept_disc,
            )

            lectures_def = COURSE_LECTURES.get(course_name, [])
            for lec_def in lectures_def:
                title, annotation, lec_type, order_num, equip_req, materials_text = (
                    lec_def
                )
                l_id = uuid.uuid4()
                self.lecture_ids[l_id] = {
                    "course_id": c_id,
                    "title": title,
                    "annotation": annotation,
                    "lecture_type": lec_type,
                    "order_number": order_num,
                    "equipment_requirements": equip_req,
                }

                await self.pg.execute(
                    "INSERT INTO lecture(id, course_id, title, annotation, lecture_type, order_number, equipment_requirements)"
                    " VALUES ($1,$2,$3,$4,$5,$6,$7)",
                    l_id,
                    c_id,
                    title,
                    annotation,
                    lec_type,
                    order_num,
                    equip_req,
                )

                lm_id = uuid.uuid4()
                lm_records.append(
                    (lm_id, l_id, "text/markdown", materials_text, None, datetime.now())
                )

                os_actions.append(
                    {
                        "_index": "lectures",
                        "_id": str(l_id),
                        "_source": {
                            "lecture_id": str(l_id),
                            "course_id": str(c_id),
                            "title": title,
                            "annotation": annotation,
                            "materials_text": materials_text,
                            "lecture_type": lec_type,
                        },
                    }
                )

        await self.pg.copy_records_to_table(
            "lecture_material",
            records=lm_records,
            columns=[
                "id",
                "lecture_id",
                "content_type",
                "content_text",
                "file_url",
                "created_at",
            ],
        )

        if os_actions:
            await helpers.async_bulk(self.os, os_actions)
        print(
            f"    Курсов: {len(self.course_ids)}, лекций: {len(self.lecture_ids)}, материалов в OpenSearch: {len(os_actions)}"
        )

    async def seed_schedule_and_attendance(self):
        print("--- Заполнение расписания и посещаемости ---")

        week_dates = [date.fromisoformat(d) for d in WEEK_DATES]

        schedule_records = []
        schedule_meta = {}

        spec_groups = {}
        for grp in STUDENT_GROUPS:
            g_id = self.group_ids.get(grp["name"])
            if g_id:
                spec_groups.setdefault(grp["specialty_code"], []).append(g_id)

        slot_idx = 0
        for dept_part, spec_code, semester, academic_year, _, _, course_name in COURSES:
            c_id = self.course_ids.get(course_name)
            if not c_id:
                continue
            groups_for_course = spec_groups.get(spec_code, [])
            if not groups_for_course:
                continue

            lectures_for_course = [
                l_id
                for l_id, meta in self.lecture_ids.items()
                if meta["course_id"] == c_id
            ]

            for lec_idx, l_id in enumerate(lectures_for_course):
                for g_id in groups_for_course:
                    for week_idx, week_date in enumerate(week_dates):
                        slot = LESSON_TIMES[
                            (slot_idx + lec_idx + week_idx) % len(LESSON_TIMES)
                        ]
                        start_t = dtime.fromisoformat(slot[0])
                        end_t = dtime.fromisoformat(slot[1])
                        classroom = CLASSROOMS[(slot_idx + lec_idx) % len(CLASSROOMS)]

                        sch_id = uuid.uuid4()
                        schedule_records.append(
                            (sch_id, l_id, g_id, week_date, start_t, end_t, classroom)
                        )
                        schedule_meta[sch_id] = (g_id, week_date)
            slot_idx += 1

        await self.pg.copy_records_to_table(
            "schedule",
            records=schedule_records,
            columns=[
                "id",
                "lecture_id",
                "group_id",
                "week_start_date",
                "start_time",
                "end_time",
                "classroom",
            ],
        )
        print(f"    Записей в расписании: {len(schedule_records)}")

        attendance_records = []
        seen = set()

        for sch_id, (g_id, week_date) in schedule_meta.items():
            students_in_group = self.group_students.get(g_id, [])
            for s_id in students_in_group:
                if random.random() < 0.70:
                    key = (sch_id, s_id)
                    if key not in seen:
                        seen.add(key)
                        marked = datetime.combine(week_date, dtime(9, 0)) + timedelta(
                            hours=random.randint(0, 8), minutes=random.randint(0, 59)
                        )
                        attendance_records.append(
                            (uuid.uuid4(), sch_id, s_id, week_date, marked)
                        )

        batch_size = 5000
        for i in range(0, len(attendance_records), batch_size):
            batch = attendance_records[i : i + batch_size]
            await self.pg.copy_records_to_table(
                "attendance",
                records=batch,
                columns=[
                    "id",
                    "schedule_id",
                    "student_id",
                    "week_start_date",
                    "marked_at",
                ],
            )
        print(f"    Записей посещаемости: {len(attendance_records)}")

        self.schedule_meta = schedule_meta
        self.schedule_records = schedule_records

    async def seed_neo4j(self):
        print("--- Заполнение Neo4j ---")
        async with self.neo4j.session() as session:
            depts = []
            for dept_name, d_id in self.dept_ids.items():
                head = ""
                for inst_depts in DEPARTMENTS.values():
                    for d in inst_depts:
                        if d["name"] == dept_name:
                            head = d["head"]
                depts.append({"id": str(d_id), "name": dept_name, "head": head})
            await session.run(
                "UNWIND $depts AS d CREATE (:Department {id: d.id, name: d.name, head: d.head})",
                depts=depts,
            )

            specs = []
            for sp in SPECIALTIES:
                sp_id = self.spec_code_to_id.get(sp["code"])
                if sp_id:
                    specs.append(
                        {
                            "id": str(sp_id),
                            "name": sp["name"],
                            "code": sp["code"],
                            "degree_level": sp["degree_level"],
                        }
                    )
            await session.run(
                "UNWIND $specs AS s CREATE (:Specialty {id: s.id, name: s.name, code: s.code, degree_level: s.degree_level})",
                specs=specs,
            )

            offers = []
            for dept_part, spec_code, is_primary in DEPT_SPECIALTY_MAP:
                d_id = self.dept_name_part_to_id.get(dept_part)
                s_id = self.spec_code_to_id.get(spec_code)
                if d_id and s_id:
                    offers.append(
                        {
                            "dept_id": str(d_id),
                            "spec_id": str(s_id),
                            "is_primary": is_primary,
                        }
                    )
            await session.run(
                """
                UNWIND $offers AS o
                MATCH (d:Department {id: o.dept_id})
                MATCH (sp:Specialty {id: o.spec_id})
                CREATE (d)-[:OFFERS_SPECIALTY {is_primary: o.is_primary}]->(sp)
                """,
                offers=offers,
            )

            courses_nodes = []
            for (
                dept_part,
                spec_code,
                semester,
                academic_year,
                total_hours,
                is_dept_disc,
                course_name,
            ) in COURSES:
                c_id = self.course_ids.get(course_name)
                if c_id:
                    courses_nodes.append(
                        {
                            "id": str(c_id),
                            "name": course_name,
                            "semester": semester,
                            "academic_year": academic_year,
                            "total_hours": total_hours,
                            "is_department_discipline": is_dept_disc,
                            "spec_id": str(self.spec_code_to_id[spec_code]),
                            "dept_id": str(self.dept_name_part_to_id[dept_part]),
                        }
                    )
            await session.run(
                "UNWIND $cs AS c CREATE (:LectureCourse {id: c.id, name: c.name, semester: c.semester, academic_year: c.academic_year, total_hours: c.total_hours, is_department_discipline: c.is_department_discipline})",
                cs=courses_nodes,
            )
            await session.run(
                """
                UNWIND $cs AS c
                MATCH (lc:LectureCourse {id: c.id})
                MATCH (sp:Specialty {id: c.spec_id})
                MATCH (d:Department {id: c.dept_id})
                CREATE (lc)-[:BELONGS_TO_SPECIALTY]->(sp)
                CREATE (lc)-[:TAUGHT_BY_DEPARTMENT]->(d)
                """,
                cs=courses_nodes,
            )

            lec_nodes = []
            for l_id, meta in self.lecture_ids.items():
                lec_nodes.append(
                    {
                        "id": str(l_id),
                        "title": meta["title"],
                        "annotation": meta["annotation"],
                        "lecture_type": meta["lecture_type"],
                        "order_number": meta["order_number"],
                        "equipment_requirements": meta["equipment_requirements"],
                        "course_id": str(meta["course_id"]),
                    }
                )
            for i in range(0, len(lec_nodes), 200):
                batch = lec_nodes[i : i + 200]
                await session.run(
                    "UNWIND $ls AS l CREATE (:Lecture {id: l.id, title: l.title, annotation: l.annotation, lecture_type: l.lecture_type, order_number: l.order_number, equipment_requirements: l.equipment_requirements})",
                    ls=batch,
                )
            await session.run(
                """
                UNWIND $ls AS l
                MATCH (lec:Lecture {id: l.id})
                MATCH (c:LectureCourse {id: l.course_id})
                CREATE (c)-[:CONTAINS_LECTURE]->(lec)
                """,
                ls=lec_nodes,
            )

            grp_nodes = []
            for grp in STUDENT_GROUPS:
                g_id = self.group_ids.get(grp["name"])
                if not g_id:
                    continue
                spec_id = self.spec_code_to_id.get(grp["specialty_code"])
                grp_nodes.append(
                    {
                        "id": str(g_id),
                        "name": grp["name"],
                        "enrollment_year": grp["enrollment_year"],
                        "curator": grp["curator"],
                        "spec_id": str(spec_id),
                    }
                )
            await session.run(
                "UNWIND $gs AS g CREATE (:StudentGroup {id: g.id, name: g.name, enrollment_year: g.enrollment_year, curator: g.curator})",
                gs=grp_nodes,
            )
            await session.run(
                """
                UNWIND $gs AS g
                MATCH (sg:StudentGroup {id: g.id})
                MATCH (sp:Specialty {id: g.spec_id})
                CREATE (sg)-[:STUDIES_SPECIALTY]->(sp)
                """,
                gs=grp_nodes,
            )

            student_nodes = [
                {
                    "id": str(s_id),
                    "first_name": info["first_name"],
                    "last_name": info["last_name"],
                    "email": info["email"],
                    "phone": info["phone"],
                    "group_id": str(info["group_id"]),
                }
                for s_id, info in self.students.items()
            ]
            for i in range(0, len(student_nodes), 500):
                batch = student_nodes[i : i + 500]
                await session.run(
                    "UNWIND $ss AS s CREATE (:Student {id: s.id, first_name: s.first_name, last_name: s.last_name, email: s.email, phone: s.phone})",
                    ss=batch,
                )
            await session.run(
                """
                UNWIND $ss AS s
                MATCH (st:Student {id: s.id})
                MATCH (g:StudentGroup {id: s.group_id})
                CREATE (st)-[:MEMBER_OF]->(g)
                """,
                ss=student_nodes,
            )

            sch_nodes = [
                {
                    "id": str(sch_id),
                    "week_start_date": str(week_date),
                    "start_time": str(start_t),
                    "end_time": str(end_t),
                    "classroom": classroom,
                    "lecture_id": str(l_id),
                    "group_id": str(g_id),
                }
                for sch_id, l_id, g_id, week_date, start_t, end_t, classroom in self.schedule_records
            ]
            for i in range(0, len(sch_nodes), 300):
                batch = sch_nodes[i : i + 300]
                await session.run(
                    "UNWIND $ss AS s CREATE (:Schedule {id: s.id, scheduled_date: s.week_start_date, start_time: s.start_time, end_time: s.end_time, classroom: s.classroom})",
                    ss=batch,
                )
            for i in range(0, len(sch_nodes), 300):
                batch = sch_nodes[i : i + 300]
                await session.run(
                    """
                    UNWIND $ss AS s
                    MATCH (sch:Schedule {id: s.id})
                    MATCH (l:Lecture {id: s.lecture_id})
                    MATCH (g:StudentGroup {id: s.group_id})
                    CREATE (sch)-[:FOR_LECTURE]->(l)
                    CREATE (sch)-[:FOR_GROUP]->(g)
                    """,
                    ss=batch,
                )

        print("    Узлы и связи Neo4j созданы")

    async def seed_redis(self):
        print("--- Заполнение Redis (профили студентов) ---")
        pipe = self.redis.pipeline(transaction=False)
        for s_id, info in self.students.items():
            key = f"student:{s_id}"
            await pipe.hset(
                key,
                mapping={
                    "first_name": info["first_name"],
                    "last_name": info["last_name"],
                    "middle_name": info["middle_name"],
                    "group_name": info["group_name"],
                    "email": info["email"],
                    "phone": info["phone"],
                },
            )
            await pipe.expire(key, 7200)
        await pipe.execute()
        print(f"    Ключей Redis: {len(self.students)}")

    async def seed_mongodb(self):
        print("--- Заполнение MongoDB (иерархия организации) ---")

        institutes_docs = []
        for inst_info in INSTITUTES:
            i_id = self.institute_ids[inst_info["name"]]
            depts_docs = []
            for dept_info in DEPARTMENTS[inst_info["name"]]:
                d_id = self.dept_ids[dept_info["name"]]
                depts_docs.append(
                    {
                        "department_id": str(d_id),
                        "name": dept_info["name"],
                        "head": dept_info["head"],
                    }
                )
            institutes_docs.append(
                {
                    "institute_id": str(i_id),
                    "name": inst_info["name"],
                    "dean": inst_info["dean"],
                    "departments": depts_docs,
                }
            )

        u = UNIVERSITIES[0]
        doc = {
            "university_id": str(self.university_id),
            "name": u["name"],
            "address": u["address"],
            "founded_year": u["founded_year"],
            "institutes": institutes_docs,
            "updated_at": datetime.now(dUTC),
        }
        await self.mongo_db["organization_structure"].insert_one(doc)
        print("    Документ организационной структуры создан")

    async def seed(self):
        await self.init_connections()
        try:
            await self.clean_all()
            await self.create_schema_pg()
            await self.create_opensearch_index()
            await self.seed_university_structure()
            await self.seed_groups_and_students()
            await self.seed_courses_and_lectures()
            await self.seed_schedule_and_attendance()
            await self.seed_neo4j()
            await self.seed_redis()
            await self.seed_mongodb()
            print("\n=== Сидирование завершено успешно! ===")
        finally:
            await self.close_connections()


if __name__ == "__main__":
    seeder = UniversitySeeder()
    asyncio.run(seeder.seed())
