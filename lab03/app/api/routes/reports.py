from fastapi import APIRouter, HTTPException, Request

from app.api.services import reports_service
from app.models.web.report import ReportRequest, ReportResponse

router = APIRouter()


@router.post("/get_report")
async def get_report(request: Request, body: ReportRequest) -> ReportResponse:
    result = await reports_service.get_dept_hours_report(
        request.app.state,
        body.group_id,
        body.semester,
        body.academic_year,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Группа не найдена или нет профильных курсов кафедры в данном семестре.",
        )
    return result
