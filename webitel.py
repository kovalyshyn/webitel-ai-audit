"""Webitel API client (async)."""
import httpx
from typing import Any


class WebitelError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"Webitel {status}: {detail}")


class WebitelClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self._headers = {"x-webitel-access": token}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _raise_for(self, r: httpx.Response) -> None:
        if r.is_error:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise WebitelError(r.status_code, detail)

    # ── Calls ────────────────────────────────────────────────────────────────

    async def get_call(self, call_uuid: str) -> dict[str, Any]:
        """Return call history record by UUID."""
        async with httpx.AsyncClient(headers=self._headers) as c:
            r = await c.get(self._url("/api/calls/history"), params={"id": call_uuid})
        self._raise_for(r)
        items = r.json().get("items", [])
        if not items:
            raise WebitelError(404, f"Call {call_uuid!r} not found")
        return items[0]

    # ── Recordings ───────────────────────────────────────────────────────────

    async def download_recording(self, file_id: str | int) -> bytes:
        """Download raw audio bytes for a recording file."""
        async with httpx.AsyncClient(headers=self._headers, follow_redirects=True) as c:
            r = await c.get(
                self._url(f"/api/storage/recordings/{file_id}/stream"),
                timeout=120,
            )
        self._raise_for(r)
        return r.content

    # ── Transcripts ──────────────────────────────────────────────────────────

    async def save_transcript(
        self,
        file_id: str | int,
        call_uuid: str,
        text: str,
        phrases: list[dict] | None = None,
        locale: str = "uk",
    ) -> dict[str, Any]:
        """Save transcript text (and optional phrases) for a recording file."""
        payload: dict[str, Any] = {
            "file_id": int(file_id),
            "uuid": call_uuid,
            "locale": locale,
            "text": text,
        }
        if phrases:
            payload["phrases"] = phrases

        async with httpx.AsyncClient(headers=self._headers) as c:
            r = await c.put(self._url("/api/storage/transcript_file"), json=payload)
        self._raise_for(r)
        return r.json()

    # ── Audit forms ──────────────────────────────────────────────────────────

    async def get_scorecard(self, form_id: int) -> dict[str, Any]:
        """Return audit form / scorecard by ID."""
        async with httpx.AsyncClient(headers=self._headers) as c:
            r = await c.get(self._url("/api/call_center/audit/forms"), params={"id": form_id})
        self._raise_for(r)
        items = r.json().get("items", [])
        if not items:
            raise WebitelError(404, f"Scorecard {form_id!r} not found")
        return items[0]

    # ── Audit rates ──────────────────────────────────────────────────────────

    async def save_audit_rate(
        self,
        call_id: str,
        form_id: int,
        form_name: str,
        answers: list[dict],
        comment: str = "",
    ) -> dict[str, Any]:
        """Submit evaluation results for a call."""
        payload: dict[str, Any] = {
            "call_id": call_id,
            "form": {"id": form_id, "name": form_name},
            "answers": answers,
        }
        if comment:
            payload["comment"] = comment

        async with httpx.AsyncClient(headers=self._headers) as c:
            r = await c.post(self._url("/api/call_center/audit/rate"), json=payload)
        self._raise_for(r)
        return r.json()
