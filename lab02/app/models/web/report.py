from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ReportRequest(BaseModel):
    course_id: UUID
    semester: int
    academic_year: int


class CourseInfo(BaseModel):
    id: UUID
    name: str
    semester: int
    academic_year: int
    total_hours: int


class SemesterInfo(BaseModel):
    semester: int
    academic_year: int


class SessionRecord(BaseModel):
    schedule_id: UUID
    session_date: str
    required_capacity: int
    lecture_id: UUID
    title: str
    lecture_type: str
    order_number: int
    equipment_requirements: str


class ReportResponse(BaseModel):
    course: CourseInfo
    semester: SemesterInfo
    sessions: list[SessionRecord]
    generated_at: datetime
