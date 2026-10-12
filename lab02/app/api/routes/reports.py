from fastapi import APIRouter, HTTPException, Request

from app.api.services import reports_service
from app.models.web.report import ReportRequest, ReportResponse

router = APIRouter()


@router.post("/get_report", response_model=ReportResponse)
async def get_report(request: Request, body: ReportRequest) -> ReportResponse:
    result = await reports_service.get_classroom_capacity_report(
        request.app.state,
        body.course_id,
        body.semester,
        body.academic_year,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Курс не найден или в данном семестре нет занятий.",
        )
    return result
