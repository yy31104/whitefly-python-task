from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.rate_limit import RateLimitExceeded, enforce_rate_limit
from shared.services import create_submission_sync, enqueue_submission_async, list_submissions
from shared.validation import ValidationError


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def _redirect_with_message(
    request: Request,
    route_name: str,
    message: str,
    category: str = "success",
) -> RedirectResponse:
    path = request.app.url_path_for(route_name)
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


def _client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client and request.client.host:
        return request.client.host
    return "unknown"


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
        enforce_rate_limit(
            identifier=_client_identifier(request),
            endpoint="fastapi:sync-form",
            limit=request.app.state.rate_limit_post_requests,
            window_seconds=request.app.state.rate_limit_window_seconds,
            redis_url=request.app.state.redis_url,
        )
        create_submission_sync(**form_data)
    except RateLimitExceeded as exc:
        response = templates.TemplateResponse(
            request=request,
            name="sync_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=429,
        )
        response.headers["Retry-After"] = str(exc.retry_after)
        return response
    except ValidationError as exc:
        return templates.TemplateResponse(
            request=request,
            name="sync_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=400,
        )

    return _redirect_with_message(request, "submissions", "Submission saved.")


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
        enforce_rate_limit(
            identifier=_client_identifier(request),
            endpoint="fastapi:async-form",
            limit=request.app.state.rate_limit_post_requests,
            window_seconds=request.app.state.rate_limit_window_seconds,
            redis_url=request.app.state.redis_url,
        )
        task_id = enqueue_submission_async(**form_data)
    except RateLimitExceeded as exc:
        response = templates.TemplateResponse(
            request=request,
            name="async_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=429,
        )
        response.headers["Retry-After"] = str(exc.retry_after)
        return response
    except ValidationError as exc:
        return templates.TemplateResponse(
            request=request,
            name="async_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=400,
        )

    return _redirect_with_message(request, "async_form_get", f"Submission queued. Task ID: {task_id}")


@router.get("/submissions", response_class=HTMLResponse)
async def submissions(
    request: Request,
    message: str | None = None,
    category: str = "success",
) -> HTMLResponse:
    if not request.app.state.enable_submissions_page:
        raise HTTPException(status_code=404, detail="Not Found")

    rows = list_submissions()
    context = {
        "submissions": rows,
        "message": message,
        "category": category,
    }
    return templates.TemplateResponse(request=request, name="submissions.html", context=context)
