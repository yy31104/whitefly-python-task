from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.services import create_submission_sync, enqueue_submission_async, list_submissions
from shared.validation import ValidationError


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def _redirect_with_message(path: str, message: str, category: str = "success") -> RedirectResponse:
    query = urlencode({"message": message, "category": category})
    return RedirectResponse(url=f"{path}?{query}", status_code=303)


def _context(
    *,
    form_data: dict[str, str] | None = None,
    message: str | None = None,
    category: str = "success",
) -> dict:
    return {
        "form_data": form_data or {},
        "message": message,
        "category": category,
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/sync-form", response_class=HTMLResponse)
async def sync_form_get(
    request: Request,
    message: str | None = None,
    category: str = "success",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="sync_form.html",
        context=_context(message=message, category=category),
    )


@router.post("/sync-form", response_class=HTMLResponse)
async def sync_form_post(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    honeypot: str = Form(""),
) -> HTMLResponse:
    form_data = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "honeypot": honeypot,
    }

    try:
        create_submission_sync(**form_data)
    except ValidationError as exc:
        return templates.TemplateResponse(
            request=request,
            name="sync_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=400,
        )

    return _redirect_with_message("/submissions", "Submission saved.")


@router.get("/async-form", response_class=HTMLResponse)
async def async_form_get(
    request: Request,
    message: str | None = None,
    category: str = "success",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="async_form.html",
        context=_context(message=message, category=category),
    )


@router.post("/async-form", response_class=HTMLResponse)
async def async_form_post(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    honeypot: str = Form(""),
) -> HTMLResponse:
    form_data = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "honeypot": honeypot,
    }

    try:
        task_id = enqueue_submission_async(**form_data)
    except ValidationError as exc:
        return templates.TemplateResponse(
            request=request,
            name="async_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=400,
        )

    return _redirect_with_message("/async-form", f"Submission queued. Task ID: {task_id}")


@router.get("/submissions", response_class=HTMLResponse)
async def submissions(
    request: Request,
    message: str | None = None,
    category: str = "success",
) -> HTMLResponse:
    rows = list_submissions()
    context = {
        "submissions": rows,
        "message": message,
        "category": category,
    }
    return templates.TemplateResponse(request=request, name="submissions.html", context=context)
