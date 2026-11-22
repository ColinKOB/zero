""



import asyncio
import logging
import mimetypes
import urllib.parse

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import config, teamfiles, teams, who

log = logging.getLogger("taffy.teamsapi")


class TextBody(BaseModel):
    text: str


class MentionBody(BaseModel):
    id: str
    name: str
    location: int = Field(ge=0, strict=True)
    length: int = Field(gt=0, strict=True)


class MessageBody(BaseModel):
    text: str = ""
    attachments: list[str] = []
    mentions: list[MentionBody] = Field(default_factory=list, max_length=100)
    to: list[str] | None = None


class AnswerBody(BaseModel):
    answer: str = ""
    attachments: list[str] = []


class PausedBody(BaseModel):
    paused: bool


def _team(tid: str) -> dict:
    try:
        return teams.get(tid)
    except teams.TeamsError as e:
        raise HTTPException(404, str(e))


def install(app, require_device, require_internal, remove_bot, internal_body) -> None:
    ""


    @app.get("/api/teams")
    def list_teams(_: str = Depends(require_device)):
        return {"teams": teams.list_teams()}

    @app.get("/api/teams/{tid}")
    def team_detail(tid: str, _: str = Depends(require_device)):
        _team(tid)
        return teams.detail(tid)

    @app.get("/api/teams/{tid}/thread")
    def team_thread(tid: str, after: int = 0, _: str = Depends(require_device)):
        _team(tid)
        return {"items": teams.thread(tid, after)}

    @app.post("/api/teams/{tid}/messages", status_code=202)
    async def team_message(tid: str, body: MessageBody, _: str = Depends(require_device)):
        _team(tid)
        text = body.text.strip()
        if not text and not body.attachments:
            raise HTTPException(400, "Type a message first.")
        if len(text) > 4000:
            raise HTTPException(400, "That message is too long.")
        if body.attachments:
            try:
                text = f"{text or 'Here is what I attached.'}\n\n{teamfiles.lines(tid, body.attachments)}"
            except teamfiles.TeamFileError as e:
                raise HTTPException(400, str(e))
        try:
            mentions = teams.checked_mentions(tid, text, [m.model_dump() for m in body.mentions], body.to)
            teams.recipients(tid, text, body.to)
        except teams.TeamsError as e:
            raise HTTPException(400, str(e))
        teams.spawn(_logged(teams.message(tid, text, body.to, **({"mentions": mentions} if mentions else {})), "team message"))
        return {"ok": True}

    @app.post("/api/teams/{tid}/uploads")
    async def team_upload(tid: str, request: Request, name: str = "", _: str = Depends(require_device)):
        ""
        _team(tid)
        from . import uploads
        too_big = HTTPException(413, "That file is over 25 MB. Send a smaller one.")
        try:
            if int(request.headers.get("content-length") or 0) > uploads.MAX_BYTES:
                raise too_big
        except ValueError:
            raise HTTPException(400, "That upload couldn't be read. Try again.")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > uploads.MAX_BYTES:
                raise too_big
            chunks.append(chunk)
        try:
            return await asyncio.to_thread(teamfiles.save, tid, name, b"".join(chunks))
        except teamfiles.TeamFileError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/teams/{tid}/files")
    def team_files(tid: str, _: str = Depends(require_device)):
        _team(tid)
        return {"files": teamfiles.listing(tid)}

    @app.get("/api/teams/{tid}/files/{path:path}")
    def team_file(tid: str, path: str, _: str = Depends(require_device)):
        _team(tid)
        try:
            f, name, size = teamfiles.open_checked(tid, path)
        except teamfiles.TeamFileError as e:
            raise HTTPException(404, str(e))

        def chunks():
            with f:
                while block := f.read(1 << 16):
                    yield block
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        quoted = urllib.parse.quote(name)
        return StreamingResponse(chunks(), media_type=mime, headers={
            "Content-Length": str(size), "Content-Disposition": f"inline; filename*=UTF-8''{quoted}"})

    @app.post("/api/teams/{tid}/preview")
    async def team_preview(tid: str, _: str = Depends(require_device)):
        ""
        _team(tid)
        try:
            return await teams.preview_site(tid)
        except teams.TeamsError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/teams/{tid}/checkin", status_code=202)
    async def team_checkin(tid: str, _: str = Depends(require_device)):
        t = _team(tid)
        if t["state"] != "active":
            raise HTTPException(409, "The team is not active right now.")
        teams.spawn(_logged(teams.checkin(tid), "team check-in"))
        return {"ok": True}

    @app.post("/api/teams/{tid}/needs/{need_id}")
    async def team_need(tid: str, need_id: int, body: AnswerBody, _: str = Depends(require_device)):
        _team(tid)
        try:
            files = teamfiles.lines(tid, body.attachments) if body.attachments else ""
        except teamfiles.TeamFileError as e:
            raise HTTPException(400, str(e))
        try:
            return teams.answer_need(tid, need_id, body.answer, files=files)
        except teams.TeamsError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/teams/{tid}/pause")
    async def team_pause(tid: str, body: PausedBody, _: str = Depends(require_device)):
        _team(tid)
        try:
            return teams.set_paused(tid, body.paused)
        except teams.TeamsError as e:
            raise HTTPException(409, str(e))

    @app.delete("/api/teams/{tid}", status_code=204)
    async def team_delete(tid: str, _: str = Depends(require_device)):
        _team(tid)
        try:
            await teams.delete(tid, remove_bot)
        except teams.TeamsError as e:
            raise HTTPException(409, str(e))
        return Response(status_code=204)

    @app.post("/api/team_proposals/{pid}/start", status_code=202)
    async def proposal_start(pid: str, _: str = Depends(require_device)):
        try:
            teams.start(pid)
        except teams.TeamsError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/team_proposals/{pid}/change", status_code=202)
    async def proposal_change(pid: str, body: TextBody, _: str = Depends(require_device)):
        try:
            teams.change(pid, body.text)
        except teams.TeamsError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/internal/team_space/{op}")
    async def internal_team_space(op: str, body: internal_body, _: None = Depends(require_internal)):
        bot = config.BOTS_BY_ID.get(body.bot_id)
        if not bot:
            raise HTTPException(404, "Unknown bot.")
        a = body.args
        lead_only = {"propose", "start_plan", "member_created", "start_failed", "started"}
        try:
            if op in lead_only and bot.id != "default":
                return {"ok": False, "error": "Only the lead proposes and starts teams."}
            if op == "propose":

                return {"ok": False, "error": teams.PROJECT_NOT_TEAM.format(person=who.first())}
            if op == "start_plan":
                return {"ok": True, "result": teams.start_plan(str(a.get("proposal_id") or ""))}
            if op == "member_created":
                teams.member_created(str(a.get("proposal_id") or ""), str(a.get("bot_id") or ""))
                return {"ok": True, "result": "noted"}
            if op == "start_failed":
                teams.start_failed(str(a.get("proposal_id") or ""), str(a.get("note") or ""))
                return {"ok": True, "result": "noted"}
            if op == "started":
                pid = str(a.get("proposal_id") or "")
                if not teams.proposal(pid):
                    return {"ok": False, "error": "That proposal is gone."}
                teams.claim_start(pid)
                teams.spawn(_logged(teams.started(pid), "team start"))
                return {"ok": True, "result": "The team is coming online."}
            if op == "milestone":
                return {"ok": True, "result": teams.update_milestone(bot.id, a)}
            if op == "need":
                return {"ok": True, "result": teams.add_need(bot.id, a, body.session_id)}
            if op == "post":
                return {"ok": True, "result": await teams.post_from_tool(bot.id, body.session_id, str(a.get("text") or ""))}
            if op == "publish":
                return {"ok": True, "result": await teams.publish(bot.id, a)}
        except teams.TeamsError as e:
            return {"ok": False, "error": str(e)}
        raise HTTPException(404, "Unknown action.")


async def _logged(coro, what: str) -> None:
    try:
        await coro
    except Exception:
        log.exception("%s failed", what)
