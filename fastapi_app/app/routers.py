from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.idempotency import generate_idempotency_key, resolve_idempotency_key
from shared.rate_limit import (
    RateLimitExceeded,
    enforce_rate_limit,
    trusted_client_identifier,
)
from shared.services import (
    IdempotencyKeyMismatch,
    IdempotencyRequestInFlight,
    QueueUnavailable,
    create_submission_sync,
    enqueue_submission_async,
    list_submissions,
)
from shared.task_status import (
    get_task_status,
    sanitize_task_id,
    serialize_task_status,
)
from shared.validation import ValidationError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)


def _redirect_with_message(
    request: Request,
    route_name: str,
    message: str,
    category: str = "success",
    task_id: str | None = None,
) -> RedirectResponse:
    path = request.app.url_path_for(route_name)
    query_params = {"message": message, "category": category}
    if task_id:
        query_params["task_id"] = task_id
    query = urlencode(query_params)
    return RedirectResponse(url=f"{path}?{query}", status_code=303)


def _context(
    *,
    form_data: dict[str, str] | None = None,
    message: str | None = None,
    category: str = "success",
    idempotency_key: str | None = None,
    task_id: str | None = None,
) -> dict:
    return {
        "form_data": form_data or {},
        "message": message,
        "category": category,
        "idempotency_key": idempotency_key or generate_idempotency_key(),
        "task_id": task_id,
    }


def _client_identifier(request: Request) -> str:
    return trusted_client_identifier(
        x_real_ip=request.headers.get("x-real-ip"),
        x_forwarded_for=request.headers.get("x-forwarded-for"),
        remote_addr=request.client.host if request.client else None,
    )


def _request_idempotency_key(request: Request, form_value: str | None) -> str | None:
    return resolve_idempotency_key(
        header=request.headers.get("idempotency-key"),
        form=form_value,
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
    idempotency_key: str = Form(""),
) -> HTMLResponse:
    form_data = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "honeypot": honeypot,
    }
    resolved_idempotency_key = _request_idempotency_key(request, idempotency_key)

    try:
        enforce_rate_limit(
            identifier=_client_identifier(request),
            endpoint="fastapi:sync-form",
            limit=request.app.state.rate_limit_post_requests,
            window_seconds=request.app.state.rate_limit_window_seconds,
            redis_url=request.app.state.redis_url,
        )
        create_submission_sync(
            **form_data,
            idempotency_key=resolved_idempotency_key,
        )
    except RateLimitExceeded as exc:
        response = templates.TemplateResponse(
            request=request,
            name="sync_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=429,
        )
        response.headers["Retry-After"] = str(exc.retry_after)
        return response
    except IdempotencyKeyMismatch:
        return templates.TemplateResponse(
            request=request,
            name="sync_form.html",
            context=_context(
                form_data=form_data,
                message="This idempotency key was already used for a different submission.",
                category="error",
            ),
            status_code=422,
        )
    except IdempotencyRequestInFlight:
        return templates.TemplateResponse(
            request=request,
            name="sync_form.html",
            context=_context(
                form_data=form_data,
                message="A submission with this idempotency key is still being processed.",
                category="error",
            ),
            status_code=409,
        )
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
    task_id: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="async_form.html",
        context=_context(
            message=message,
            category=category,
            task_id=sanitize_task_id(task_id),
        ),
    )


@router.post("/async-form", response_class=HTMLResponse)
async def async_form_post(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    honeypot: str = Form(""),
    idempotency_key: str = Form(""),
) -> HTMLResponse:
    form_data = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "honeypot": honeypot,
    }
    resolved_idempotency_key = _request_idempotency_key(request, idempotency_key)

    try:
        enforce_rate_limit(
            identifier=_client_identifier(request),
            endpoint="fastapi:async-form",
            limit=request.app.state.rate_limit_post_requests,
            window_seconds=request.app.state.rate_limit_window_seconds,
            redis_url=request.app.state.redis_url,
        )
        task_id = enqueue_submission_async(
            **form_data,
            idempotency_key=resolved_idempotency_key,
        )
    except RateLimitExceeded as exc:
        response = templates.TemplateResponse(
            request=request,
            name="async_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=429,
        )
        response.headers["Retry-After"] = str(exc.retry_after)
        return response
    except IdempotencyKeyMismatch:
        return templates.TemplateResponse(
            request=request,
            name="async_form.html",
            context=_context(
                form_data=form_data,
                message="This idempotency key was already used for a different submission.",
                category="error",
            ),
            status_code=422,
        )
    except IdempotencyRequestInFlight:
        return templates.TemplateResponse(
            request=request,
            name="async_form.html",
            context=_context(
                form_data=form_data,
                message="A submission with this idempotency key is still being processed.",
                category="error",
            ),
            status_code=409,
        )
    except QueueUnavailable as exc:
        return templates.TemplateResponse(
            request=request,
            name="async_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=503,
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request=request,
            name="async_form.html",
            context=_context(form_data=form_data, message=str(exc), category="error"),
            status_code=400,
        )

    return _redirect_with_message(
        request,
        "async_form_get",
        f"Submission queued. Task ID: {task_id}",
        task_id=task_id,
    )


@router.get("/async-status/{task_id}")
async def async_status(task_id: str) -> dict:
    sanitized_task_id = sanitize_task_id(task_id)
    if sanitized_task_id is None:
        raise HTTPException(status_code=400, detail="Invalid task id.")

    task_status = get_task_status(sanitized_task_id)
    if task_status is None:
        raise HTTPException(status_code=404, detail="Task status not found.")

    return serialize_task_status(task_status)


@router.get("/submissions", response_class=HTMLResponse)
async def submissions(
    request: Request,
    message: str | None = None,
    category: str = "success",
) -> HTMLResponse:
    if not request.app.state.enable_submissions_page:
        raise HTTPException(status_code=404, detail="Not Found")

    rows = list_submissions(limit=request.app.state.submissions_page_limit)
    context = {
        "submissions": rows,
        "message": message,
        "category": category,
    }
    return templates.TemplateResponse(
        request=request, name="submissions.html", context=context
    )
