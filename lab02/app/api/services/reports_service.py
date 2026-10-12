import logging
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from neo4j import AsyncDriver

from app.models.web.report import (
    CourseInfo,
    ReportResponse,
    SemesterInfo,
    SessionRecord,
)

logger = logging.getLogger(__name__)


async def _find_sessions_neo4j(
    driver: AsyncDriver,
    course_id: UUID,
    semester: int,
    academic_year: int,
) -> list[dict]:
    """
    Neo4j: по course_id с проверкой semester/academic_year найти все занятия курса.
    Для каждой записи расписания считаем required_capacity = count(DISTINCT Student).
    Возвращает list[{course_id, course_name, semester, academic_year,
    total_hours, schedule_id, session_date, lecture_id, required_capacity}].
    """
    query = """
    MATCH (c:LectureCourse {id: $course_id})
    WHERE c.semester = $semester AND c.academic_year = $academic_year
    MATCH (c)-[:CONTAINS_LECTURE]->(l:Lecture)
    MATCH (sch:Schedule)-[:FOR_LECTURE]->(l)
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
    """
    async with driver.session() as session:
        result = await session.run(
            query,
            course_id=str(course_id),
            semester=semester,
            academic_year=academic_year,
        )
        return await result.data()


async def _get_lecture_details_pg(
    pool: asyncpg.Pool,
    lecture_ids: list[str],
) -> dict[str, dict]:
    """
    PostgreSQL: title, lecture_type, order_number, equipment_requirements
    для каждого lecture_id из списка.
    Возвращает {lecture_id_str: {title, lecture_type, order_number, equipment_requirements}}.
    """
    lecture_uuids = [UUID(lid) for lid in lecture_ids]
    rows = await pool.fetch(
        """
        SELECT id::text,
               title,
               lecture_type,
               order_number,
               equipment_requirements
        FROM lecture
        WHERE id = ANY($1::uuid[])
        """,
        lecture_uuids,
    )
    return {row["id"]: dict(row) for row in rows}


async def get_classroom_capacity_report(
    state,
    course_id: UUID,
    semester: int,
    academic_year: int,
) -> ReportResponse | None:
    """
    Алгоритм ЛР №2:
      1. Neo4j -> все занятия курса заданного семестра и года + count(students) на занятие
      2. PostgreSQL -> метаданные лекций (title, type, order, equipment)
      3. Сборка ответа
    Возвращает None, если курс не найден или в семестре нет занятий.
    """
    neo4j_rows = await _find_sessions_neo4j(
        state.neo4j, course_id, semester, academic_year
    )
    logger.info(
        "Neo4j: course_id=%s semester=%d/%d → %d сессий",
        course_id,
        semester,
        academic_year,
        len(neo4j_rows),
    )
    if not neo4j_rows:
        return None

    first = neo4j_rows[0]
    course_info = CourseInfo(
        id=UUID(first["course_id"]),
        name=first["course_name"],
        semester=int(first["semester"]),
        academic_year=int(first["academic_year"]),
        total_hours=int(first["total_hours"]),
    )

    lecture_ids = list({row["lecture_id"] for row in neo4j_rows})
    lecture_details = await _get_lecture_details_pg(state.pg_pool, lecture_ids)
    logger.info("PostgreSQL: метаданные для %d лекций", len(lecture_details))

    sessions: list[SessionRecord] = []
    for row in neo4j_rows:
        lid = row["lecture_id"]
        details = lecture_details.get(lid, {})
        sessions.append(
            SessionRecord(
                schedule_id=UUID(row["schedule_id"]),
                session_date=str(row["session_date"]),
                required_capacity=int(row["required_capacity"]),
                lecture_id=UUID(lid),
                title=details.get("title", ""),
                lecture_type=details.get("lecture_type", ""),
                order_number=int(details.get("order_number", 0)),
                equipment_requirements=details.get("equipment_requirements", ""),
            )
        )

    return ReportResponse(
        course=course_info,
        semester=SemesterInfo(semester=semester, academic_year=academic_year),
        sessions=sessions,
        generated_at=datetime.now(timezone.utc),
    )
