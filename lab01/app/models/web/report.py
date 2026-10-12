from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class ReportRequest(BaseModel):
    term: str
    date_from: date
    date_to: date


class StudentAttendanceRecord(BaseModel):
    student_id: UUID
    last_name: str
    first_name: str
    middle_name: str
    email: str
    phone: str
    group_name: str
    enrollment_date: date
    attendance_percent: float
    total_lecture_events: int
    present_count: int
    specialty_name: str
    department_name: str
    institute_name: str
    university_name: str
    report_period: str
    term: str


class ReportResponse(BaseModel):
    term: str
    date_from: date
    date_to: date
    generated_at: datetime
    students: list[StudentAttendanceRecord]
    total_found: int
