from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ReportRequest(BaseModel):
    group_id: UUID
    semester: int
    academic_year: int


class GroupInfo(BaseModel):
    id: UUID
    name: str
    enrollment_year: int
    curator: str


class SemesterInfo(BaseModel):
    semester: int
    academic_year: int


class StudentHours(BaseModel):
    student_id: UUID
    first_name: str
    last_name: str
    middle_name: str
    email: str
    phone: str
    specialty_name: str
    department_name: str
    institute_name: str
    university_name: str
    attended_hours: int


class CourseHours(BaseModel):
    id: UUID
    name: str
    semester: int
    academic_year: int
    planned_hours: int
    department_name: str
    students: list[StudentHours]


class ReportResponse(BaseModel):
    group: GroupInfo
    semester: SemesterInfo
    courses: list[CourseHours]
    generated_at: datetime
