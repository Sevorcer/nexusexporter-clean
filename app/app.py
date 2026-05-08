import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import SECRET_KEY
from app.db import create_db
from app.routers.accounts import router as accounts_router
from app.routers.api import router as api_router
from app.routers.pages import router as pages_router


def create_app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
    application.mount("/static", StaticFiles(directory="static"), name="static")

    create_db()

    application.include_router(api_router)
    application.include_router(accounts_router)
    application.include_router(pages_router)

    @application.exception_handler(Exception)
    def global_exception_handler(request: Request, exc: Exception):
        logging.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return HTMLResponse(
            "<h1>Something went wrong</h1>"
            "<p>Our team has been notified. Please try again or head back to the dashboard.</p>"
            "<p><a href='/'>Back to dashboard</a></p>",
            status_code=500,
        )

    return application


app = create_app()
