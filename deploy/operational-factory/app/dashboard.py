from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx

from app.settings import TOOL_GATEWAY_URL

app = FastAPI(title="NAVIG Approval Inbox", version="0.1.0")
templates = Jinja2Templates(directory="/srv/app/app/templates")


@app.get("/health")
def health():
    return {"ok": True, "service": "dashboard"}


@app.get("/", response_class=HTMLResponse)
def inbox(request: Request):
    data = httpx.get(f"{TOOL_GATEWAY_URL}/approval/inbox", timeout=20).json()
    return templates.TemplateResponse("inbox.html", {"request": request, "actions": data.get("actions", []), "drafts": data.get("drafts", [])})


@app.post("/approve/{action_id}")
def approve(action_id: str, decided_by: str = Form("owner"), notes: str = Form("")):
    httpx.post(
        f"{TOOL_GATEWAY_URL}/approval/{action_id}/decision",
        json={"decision": "approved", "decided_by": decided_by, "notes": notes},
        timeout=20,
    ).raise_for_status()
    return RedirectResponse(url="/", status_code=303)


@app.post("/reject/{action_id}")
def reject(action_id: str, decided_by: str = Form("owner"), notes: str = Form("")):
    httpx.post(
        f"{TOOL_GATEWAY_URL}/approval/{action_id}/decision",
        json={"decision": "rejected", "decided_by": decided_by, "notes": notes},
        timeout=20,
    ).raise_for_status()
    return RedirectResponse(url="/", status_code=303)
