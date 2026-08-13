"""Reusable email templates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import EmailTemplate
from ..security import require_user
from ..services.rendering import find_variables
from ..web import flash, redirect, render

router = APIRouter(prefix="/templates", dependencies=[Depends(require_user)])

SAMPLE_HTML = """<p>Hello {{ first_name or name }},</p>

<p>Write your message here.</p>

<p>Kind regards,<br>The team</p>

<hr>
<p style="font-size:12px;color:#777">
  Don't want these emails? <a href="{{ unsubscribe_url }}">Unsubscribe</a>.
</p>
"""


@router.get("")
def index(request: Request, db: Session = Depends(get_db)):
    items = list(db.scalars(select(EmailTemplate).order_by(EmailTemplate.name)))
    return render(request, "templates/index.html", {"templates_list": items})


@router.get("/new")
def new_form(request: Request):
    return render(
        request,
        "templates/edit.html",
        {"item": None, "sample_html": SAMPLE_HTML},
    )


@router.post("/new")
def create(
    request: Request,
    name: str = Form(...),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        flash(request, "The template needs a name.", "error")
        return redirect("/templates/new")
    if db.scalar(select(EmailTemplate).where(func.lower(EmailTemplate.name) == name.lower())):
        flash(request, f"A template named '{name}' already exists.", "error")
        return redirect("/templates/new")

    item = EmailTemplate(
        name=name, subject=subject, body_html=body_html, body_text=body_text
    )
    db.add(item)
    db.commit()
    flash(request, f"Template '{name}' created.", "success")
    return redirect(f"/templates/{item.id}")


@router.get("/{template_id}")
def edit_form(request: Request, template_id: int, db: Session = Depends(get_db)):
    item = db.get(EmailTemplate, template_id)
    if item is None:
        flash(request, "Template not found.", "error")
        return redirect("/templates")
    return render(
        request,
        "templates/edit.html",
        {
            "item": item,
            "sample_html": SAMPLE_HTML,
            "variables": find_variables(item.subject, item.body_html, item.body_text),
        },
    )


@router.post("/{template_id}")
def update(
    request: Request,
    template_id: int,
    name: str = Form(...),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    db: Session = Depends(get_db),
):
    item = db.get(EmailTemplate, template_id)
    if item is None:
        flash(request, "Template not found.", "error")
        return redirect("/templates")
    item.name = name.strip() or item.name
    item.subject = subject
    item.body_html = body_html
    item.body_text = body_text
    db.commit()
    flash(request, "Template saved.", "success")
    return redirect(f"/templates/{template_id}")


@router.post("/{template_id}/delete")
def delete(request: Request, template_id: int, db: Session = Depends(get_db)):
    item = db.get(EmailTemplate, template_id)
    if item is not None:
        db.delete(item)
        db.commit()
        flash(request, f"Template '{item.name}' deleted.", "success")
    return redirect("/templates")
