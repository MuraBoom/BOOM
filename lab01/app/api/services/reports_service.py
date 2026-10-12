import logging
from datetime import date, datetime, timezone
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis
from motor.motor_asyncio import AsyncIOMotorDatabase
from neo4j import AsyncDriver
from opensearchpy import AsyncOpenSearch

from app.models.web.report import ReportResponse, StudentAttendanceRecord

logger = logging.getLogger(__name__)


async def _search_lectures_opensearch(client: AsyncOpenSearch, term: str) -> list[str]:
    """OpenSearch: multi_match по term → список lecture_id."""
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


async def _find_students_neo4j(
    driver: AsyncDriver,
    lecture_ids: list[str],
    date_from: date,
    date_to: date,
) -> list[dict]:
    """
    Neo4j: по lecture_ids и периоду найти студентов через граф расписания.
    Возвращает list[{student_id, group_name, specialty_name, department_id, total_lecture_events}].
    """
    query = """
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
    """
    async with driver.session() as session:
        result = await session.run(
            query,
            lecture_ids=lecture_ids,
            date_from=str(date_from),
            date_to=str(date_to),
        )
        return await result.data()


async def _count_attendance_pg(
    pool: asyncpg.Pool,
    student_ids: list[str],
    lecture_ids: list[str],
    date_from: date,
    date_to: date,
) -> dict[str, dict]:
    """
    PostgreSQL: считает present_count и берёт enrollment_date для каждого студента.
    Включает студентов с 0 посещений (LEFT JOIN).
    Возвращает {student_id_str: {present_count, enrollment_date}}.
    """
    student_uuids = [UUID(s) for s in student_ids]
    lecture_uuids = [UUID(lecture_id) for lecture_id in lecture_ids]

    rows = await pool.fetch(
        """
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
        """,
        student_uuids,
        lecture_uuids,
        date_from,
        date_to,
    )
    return {
        row["student_id"]: {
            "present_count": int(row["present_count"]),
            "enrollment_date": row["enrollment_date"],
        }
        for row in rows
    }


async def _get_student_profiles_redis(
    client: aioredis.Redis,
    student_ids: list[str],
) -> dict[str, dict]:
    """
    Redis: HGETALL student:{id} для каждого студента через pipeline.
    Возвращает {student_id_str: {first_name, last_name, middle_name, email, phone}}.
    """
    pipe = client.pipeline(transaction=False)
    for s_id in student_ids:
        pipe.hgetall(f"student:{s_id}")
    results = await pipe.execute()
    return {s_id: (result or {}) for s_id, result in zip(student_ids, results)}


async def _get_org_lookup_mongo(db: AsyncIOMotorDatabase) -> dict[str, dict]:
    """
    MongoDB: читает один документ organization_structure и строит lookup-таблицу.
    Возвращает {department_id_str: {department_name, institute_name, university_name}}.
    """
    doc = await db["organization_structure"].find_one({})
    if not doc:
        return {}

    lookup: dict[str, dict] = {}
    univ_name: str = doc.get("name", "")
    for inst in doc.get("institutes", []):
        inst_name: str = inst.get("name", "")
        for dept in inst.get("departments", []):
            dept_id: str = dept.get("department_id", "")
            lookup[dept_id] = {
                "department_name": dept.get("name", ""),
                "institute_name": inst_name,
                "university_name": univ_name,
            }
    return lookup


async def get_low_attendance_report(
    state,
    term: str,
    date_from: date,
    date_to: date,
) -> ReportResponse:
    """
    Алгоритм ЛР №1:
      1. OpenSearch -> lecture_ids по термину
      2. Neo4j -> student_ids + группа + специальность + кафедра + кол-во занятий
      3. PostgreSQL -> кол-во посещений + дата зачисления
      4. Redis -> ФИО + контакты
      5. MongoDB -> иерархия кафедра→институт→университет
      6. Сборка, расчёт %, сортировка, топ-10
    """
    empty = ReportResponse(
        term=term,
        date_from=date_from,
        date_to=date_to,
        generated_at=datetime.now(timezone.utc),
        students=[],
        total_found=0,
    )

    # 1 — OpenSearch
    lecture_ids = await _search_lectures_opensearch(state.opensearch, term)
    logger.info("OpenSearch: term=%r → %d лекций", term, len(lecture_ids))
    if not lecture_ids:
        return empty

    # 2 — Neo4j
    neo4j_rows = await _find_students_neo4j(
        state.neo4j, lecture_ids, date_from, date_to
    )
    logger.info("Neo4j: %d строк студентов", len(neo4j_rows))
    if not neo4j_rows:
        return empty

    # Дедупликация: один student_id → запись с максимальным total_lecture_events
    student_neo4j: dict[str, dict] = {}
    for row in neo4j_rows:
        s_id: str = row["student_id"]
        if (
            s_id not in student_neo4j
            or row["total_lecture_events"] > student_neo4j[s_id]["total_lecture_events"]
        ):
            student_neo4j[s_id] = row

    student_ids = list(student_neo4j.keys())

    # 3 — PostgreSQL
    pg_data = await _count_attendance_pg(
        state.pg_pool, student_ids, lecture_ids, date_from, date_to
    )
    logger.info("PostgreSQL: данные посещаемости для %d студентов", len(pg_data))

    # 4 — Redis
    profiles = await _get_student_profiles_redis(state.redis, student_ids)

    # 5 — MongoDB
    org_lookup = await _get_org_lookup_mongo(state.mongo)

    # 6 — Сборка
    report_period = f"{date_from}..{date_to}"
    records: list[StudentAttendanceRecord] = []

    for s_id, neo4j_row in student_neo4j.items():
        pg_row = pg_data.get(s_id, {"present_count": 0, "enrollment_date": date_from})
        profile = profiles.get(s_id, {})
        dept_id: str = neo4j_row.get("department_id", "")
        org = org_lookup.get(dept_id, {})

        total: int = neo4j_row["total_lecture_events"]
        present: int = pg_row["present_count"]
        pct = round(present / total * 100, 2) if total > 0 else 0.0

        records.append(
            StudentAttendanceRecord(
                student_id=UUID(s_id),
                last_name=profile.get("last_name", ""),
                first_name=profile.get("first_name", ""),
                middle_name=profile.get("middle_name", ""),
                email=profile.get("email", ""),
                phone=profile.get("phone", ""),
                group_name=neo4j_row["group_name"],
                enrollment_date=pg_row["enrollment_date"],
                attendance_percent=pct,
                total_lecture_events=total,
                present_count=present,
                specialty_name=neo4j_row["specialty_name"],
                department_name=org.get("department_name", ""),
                institute_name=org.get("institute_name", ""),
                university_name=org.get("university_name", ""),
                report_period=report_period,
                term=term,
            )
        )

    records.sort(key=lambda r: (r.attendance_percent, r.last_name))
    top_10 = records[:10]

    return ReportResponse(
        term=term,
        date_from=date_from,
        date_to=date_to,
        generated_at=datetime.now(timezone.utc),
        students=top_10,
        total_found=len(records),
    )
