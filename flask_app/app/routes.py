from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from flask_app.app.forms import extract_async_form_data, extract_sync_form_data
from shared.idempotency import generate_idempotency_key, resolve_idempotency_key
from shared.rate_limit import (
    RateLimitExceeded,
    enforce_rate_limit,
    trusted_client_identifier,
)
from shared.services import (
    IdempotencyConflict,
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

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/healthz")
def healthz():
    return {"status": "ok"}


def _client_identifier() -> str:
    return trusted_client_identifier(
        x_real_ip=request.headers.get("X-Real-IP"),
        x_forwarded_for=request.headers.get("X-Forwarded-For"),
        remote_addr=request.remote_addr,
    )


def _request_idempotency_key() -> str | None:
    return resolve_idempotency_key(
        header=request.headers.get("Idempotency-Key"),
        form=request.form.get("idempotency_key"),
    )


def _fresh_form_idempotency_key() -> str:
    return generate_idempotency_key()


@bp.route("/sync-form", methods=["GET", "POST"])
def sync_form():
    if request.method == "POST":
        form_data = extract_sync_form_data(request.form)
        idempotency_key = _request_idempotency_key()
        try:
            enforce_rate_limit(
                identifier=_client_identifier(),
                endpoint="flask:sync-form",
                limit=current_app.config["RATE_LIMIT_POST_REQUESTS"],
                window_seconds=current_app.config["RATE_LIMIT_WINDOW_SECONDS"],
                redis_url=current_app.config.get("REDIS_URL"),
            )
            create_submission_sync(**form_data, idempotency_key=idempotency_key)
        except RateLimitExceeded as exc:
            flash(str(exc), "error")
            response = make_response(
                render_template(
                    "sync_form.html",
                    form_data=form_data,
                    idempotency_key=_fresh_form_idempotency_key(),
                ),
                429,
            )
            response.headers["Retry-After"] = str(exc.retry_after)
            return response
        except IdempotencyConflict:
            flash("Submission conflicts with a previous idempotency key.", "error")
            return (
                render_template(
                    "sync_form.html",
                    form_data=form_data,
                    idempotency_key=_fresh_form_idempotency_key(),
                ),
                409,
            )
        except ValidationError as exc:
            flash(str(exc), "error")
            return (
                render_template(
                    "sync_form.html",
                    form_data=form_data,
                    idempotency_key=_fresh_form_idempotency_key(),
                ),
                400,
            )

        flash("Submission saved.", "success")
        return redirect(url_for("main.submissions"))

    return render_template(
        "sync_form.html",
        form_data={},
        idempotency_key=_fresh_form_idempotency_key(),
    )


@bp.route("/async-form", methods=["GET", "POST"])
def async_form():
    if request.method == "POST":
        form_data = extract_async_form_data(request.form)
        idempotency_key = _request_idempotency_key()
        try:
            enforce_rate_limit(
                identifier=_client_identifier(),
                endpoint="flask:async-form",
                limit=current_app.config["RATE_LIMIT_POST_REQUESTS"],
                window_seconds=current_app.config["RATE_LIMIT_WINDOW_SECONDS"],
                redis_url=current_app.config.get("REDIS_URL"),
            )
            task_id = enqueue_submission_async(
                **form_data,
                idempotency_key=idempotency_key,
            )
        except RateLimitExceeded as exc:
            flash(str(exc), "error")
            response = make_response(
                render_template(
                    "async_form.html",
                    form_data=form_data,
                    idempotency_key=_fresh_form_idempotency_key(),
                ),
                429,
            )
            response.headers["Retry-After"] = str(exc.retry_after)
            return response
        except QueueUnavailable as exc:
            flash(str(exc), "error")
            return (
                render_template(
                    "async_form.html",
                    form_data=form_data,
                    idempotency_key=_fresh_form_idempotency_key(),
                ),
                503,
            )
        except ValidationError as exc:
            flash(str(exc), "error")
            return (
                render_template(
                    "async_form.html",
                    form_data=form_data,
                    idempotency_key=_fresh_form_idempotency_key(),
                ),
                400,
            )

        flash(f"Submission queued. Task ID: {task_id}", "success")
        return redirect(url_for("main.async_form"))

    return render_template(
        "async_form.html",
        form_data={},
        idempotency_key=_fresh_form_idempotency_key(),
    )


@bp.get("/async-status/<task_id>")
def async_status(task_id: str):
    sanitized_task_id = sanitize_task_id(task_id)
    if sanitized_task_id is None:
        return jsonify({"detail": "Invalid task id."}), 400

    task_status = get_task_status(sanitized_task_id)
    if task_status is None:
        return jsonify({"detail": "Task status not found."}), 404

    return jsonify(serialize_task_status(task_status))


@bp.get("/submissions")
def submissions():
    if not current_app.config.get("ENABLE_SUBMISSIONS_PAGE", True):
        abort(404)

    rows = list_submissions(limit=current_app.config.get("SUBMISSIONS_PAGE_LIMIT", 200))
    return render_template("submissions.html", submissions=rows)
