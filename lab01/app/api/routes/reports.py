from fastapi import APIRouter, Request

from app.api.services import reports_service
from app.models.web.report import ReportRequest, ReportResponse

router = APIRouter()


@router.post("/get_report")
async def get_report(request: Request, body: ReportRequest) -> ReportResponse:
    return await reports_service.get_low_attendance_report(
        request.app.state,
        body.term,
        body.date_from,
        body.date_to,
    )
