from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from flask_app.app.forms import extract_sync_form_data
from shared.services import create_submission_sync, list_submissions
from shared.validation import ValidationError

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.route("/sync-form", methods=["GET", "POST"])
def sync_form():
    if request.method == "POST":
        form_data = extract_sync_form_data(request.form)
        try:
            create_submission_sync(**form_data)
        except ValidationError as exc:
            flash(str(exc), "error")
            return render_template("sync_form.html", form_data=form_data), 400

        flash("Submission saved.", "success")
        return redirect(url_for("main.submissions"))

    return render_template("sync_form.html", form_data={})


@bp.get("/submissions")
def submissions():
    rows = list_submissions()
    return render_template("submissions.html", submissions=rows)
