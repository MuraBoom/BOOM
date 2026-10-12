import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis
from motor.motor_asyncio import AsyncIOMotorDatabase
from neo4j import AsyncDriver

from app.models.web.report import (
    CourseHours,
    GroupInfo,
    ReportResponse,
    SemesterInfo,
    StudentHours,
)

logger = logging.getLogger(__name__)


async def _find_group_neo4j(
    driver: AsyncDriver,
    group_id: UUID,
    semester: int,
    academic_year: int,
) -> list[dict]:
    """
    Neo4j: по group_id найти группу, специальность, всех студентов и
    все курсы первичной кафедры заданного семестра и года обучения.
    Возвращает list[{group_id, group_name, enrollment_year, curator,
    specialty_name, department_id, department_name, course_id, total_hours, student_ids}].
    """
    query = """
    MATCH (g:StudentGroup {id: $group_id})
    MATCH (g)-[:STUDIES_SPECIALTY]->(sp:Specialty)
    MATCH (g)<-[:MEMBER_OF]-(s:Student)
    WITH g, sp, collect(DISTINCT s.id) AS student_ids
    MATCH (dept:Department)-[:OFFERS_SPECIALTY {is_primary: true}]->(sp)
    MATCH (c:LectureCourse)-[:TAUGHT_BY_DEPARTMENT]->(dept)
    WHERE c.semester = $semester AND c.academic_year = $academic_year
    RETURN g.id               AS group_id,
           g.name             AS group_name,
           g.enrollment_year  AS enrollment_year,
           g.curator          AS curator,
           sp.name            AS specialty_name,
           dept.id            AS department_id,
           dept.name          AS department_name,
           c.id               AS course_id,
           c.total_hours      AS total_hours,
           student_ids
    """
    async with driver.session() as session:
        result = await session.run(
            query,
            group_id=str(group_id),
            semester=semester,
            academic_year=academic_year,
        )
        return await result.data()


async def _filter_dept_courses_pg(
    pool: asyncpg.Pool,
    course_ids: list[str],
) -> dict[str, dict]:
    """
    PostgreSQL: из переданных course_ids оставить только is_department_discipline=TRUE.
    Возвращает {course_id_str: {name, semester, academic_year}}.
    """
    uuids = [UUID(c) for c in course_ids]
    rows = await pool.fetch(
        """
        SELECT id::text, name, semester, academic_year
        FROM lecture_course
        WHERE id = ANY($1::uuid[]) AND is_department_discipline = TRUE
        """,
        uuids,
    )
    return {row["id"]: dict(row) for row in rows}


async def _get_attended_hours_pg(
    pool: asyncpg.Pool,
    student_ids: list[str],
    course_ids: list[str],
) -> dict[tuple[str, str], int]:
    """
    PostgreSQL: посещённые часы = count(посещённых лекций) * 2
    для каждой пары (student_id, course_id).
    Курсы уже ограничены нужным семестром и годом на этапе Neo4j.
    Возвращает {(student_id_str, course_id_str): attended_hours}.
    """
    s_uuids = [UUID(s) for s in student_ids]
    c_uuids = [UUID(c) for c in course_ids]
    rows = await pool.fetch(
        """
        SELECT a.student_id::text,
               l.course_id::text,
               COUNT(*) * 2 AS attended_hours
        FROM attendance a
        JOIN schedule sch
            ON  a.schedule_id     = sch.id
            AND a.week_start_date = sch.week_start_date
        JOIN lecture l ON sch.lecture_id = l.id
        WHERE a.student_id = ANY($1::uuid[])
          AND l.course_id  = ANY($2::uuid[])
        GROUP BY a.student_id, l.course_id
        """,
        s_uuids,
        c_uuids,
    )
    return {
        (row["student_id"], row["course_id"]): int(row["attended_hours"])
        for row in rows
    }


async def _get_student_profiles_redis(
    client: aioredis.Redis,
    student_ids: list[str],
) -> dict[str, dict]:
    """Redis: HGETALL student:{id} через pipeline."""
    pipe = client.pipeline(transaction=False)
    for sid in student_ids:
        pipe.hgetall(f"student:{sid}")
    results = await pipe.execute()
    return {sid: (result or {}) for sid, result in zip(student_ids, results)}


async def _get_org_lookup_mongo(db: AsyncIOMotorDatabase) -> dict[str, dict]:
    """
    MongoDB: читает organization_structure и строит lookup.
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


async def get_dept_hours_report(
    state,
    group_id: UUID,
    semester: int,
    academic_year: int,
) -> ReportResponse | None:
    """
    Алгоритм ЛР №3:
      1. Neo4j -> группа, специальность, студенты, курсы первичной кафедры
                  для заданного семестра и года обучения
      2. PostgreSQL -> фильтр is_department_discipline + посещённые часы (student, course)
      3. Redis -> ФИО + контакты студентов
      4. MongoDB -> иерархия кафедра → институт → университет
      5. Сборка
    """
    neo4j_rows = await _find_group_neo4j(state.neo4j, group_id, semester, academic_year)
    logger.info("Neo4j: group_id=%s → %d строк курсов", group_id, len(neo4j_rows))
    if not neo4j_rows:
        return None

    first = neo4j_rows[0]
    group_id_str: str = first["group_id"]
    group_name: str = first["group_name"]
    enrollment_year = int(first["enrollment_year"])
    curator: str = first["curator"] or ""
    specialty_name: str = first["specialty_name"]
    department_id: str = first["department_id"]
    student_ids: list[str] = list(first["student_ids"])

    neo4j_courses: dict[str, dict] = {
        row["course_id"]: {
            "total_hours": row["total_hours"],
            "department_name": row["department_name"],
        }
        for row in neo4j_rows
    }
    course_ids = list(neo4j_courses.keys())

    filtered_courses, attended = await asyncio.gather(
        _filter_dept_courses_pg(state.pg_pool, course_ids),
        _get_attended_hours_pg(state.pg_pool, student_ids, course_ids),
    )
    logger.info(
        "PostgreSQL: %d профильных курсов, %d пар (student, course) с посещением",
        len(filtered_courses),
        len(attended),
    )

    profiles = await _get_student_profiles_redis(state.redis, student_ids)

    org_lookup = await _get_org_lookup_mongo(state.mongo)
    org = org_lookup.get(department_id, {})

    courses_out: list[CourseHours] = []
    for cid, pg_meta in filtered_courses.items():
        neo4j_meta = neo4j_courses[cid]
        students_out: list[StudentHours] = []
        for sid in student_ids:
            profile = profiles.get(sid, {})
            ah = attended.get((sid, cid), 0)
            students_out.append(
                StudentHours(
                    student_id=UUID(sid),
                    first_name=profile.get("first_name", ""),
                    last_name=profile.get("last_name", ""),
                    middle_name=profile.get("middle_name", ""),
                    email=profile.get("email", ""),
                    phone=profile.get("phone", ""),
                    specialty_name=specialty_name,
                    department_name=org.get(
                        "department_name", neo4j_meta["department_name"]
                    ),
                    institute_name=org.get("institute_name", ""),
                    university_name=org.get("university_name", ""),
                    attended_hours=ah,
                )
            )
        courses_out.append(
            CourseHours(
                id=UUID(cid),
                name=pg_meta["name"],
                semester=int(pg_meta["semester"]),
                academic_year=int(pg_meta["academic_year"]),
                planned_hours=int(neo4j_meta["total_hours"]),
                department_name=neo4j_meta["department_name"],
                students=students_out,
            )
        )

    return ReportResponse(
        group=GroupInfo(
            id=UUID(group_id_str),
            name=group_name,
            enrollment_year=enrollment_year,
            curator=curator,
        ),
        semester=SemesterInfo(semester=semester, academic_year=academic_year),
        courses=courses_out,
        generated_at=datetime.now(timezone.utc),
    )
