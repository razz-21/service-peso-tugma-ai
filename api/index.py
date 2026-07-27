"""Vercel serverless entrypoint.

Vercel's Python runtime discovers ASGI apps exported as `app` from files under
`api/`. All routes are rewritten to this function (see vercel.json), so the full
FastAPI app is served from here. The heavy ML libraries were removed from the
bundle (embeddings call a hosted endpoint; uploads go to Vercel Blob) so the
function stays under Vercel's size limit.
"""

from app.main import app

__all__ = ["app"]
