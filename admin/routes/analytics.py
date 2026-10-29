from fastapi import APIRouter, Request, Form, Query, HTTPException
from fastapi.responses import RedirectResponse
from admin.auth import get_csrf_token, validate_csrf_token, translate
from admin.templating import templates
from admin.services import analytics

router = APIRouter()


@router.get('/')
def dashboard(request: Request, days: int = Query(30, ge=0, le=36500), ranking: str = 'sales', zero_only: bool = False):
    ranking = ranking if ranking in ('sales','revenue') else 'sales'
    data = analytics.report(days,ranking)
    if zero_only:
        data['listings'] = [item for item in data['listings'] if item['sales']==0]
    return templates.TemplateResponse(request=request,name='analytics.html',context={
        'request':request, **data, 'days':days,'ranking':ranking,'zero_only':zero_only,
        'csrf_token':get_csrf_token(), '_':translate})


@router.post('/refresh')
def refresh(csrf_token: str = Form('')):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    analytics.refresh()
    return RedirectResponse('/analytics/',status_code=303)
