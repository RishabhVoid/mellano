"""One-origin production viewer and API; no automatic compute workers."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sim.api import app as api
from sim.config import ROOT, WEB_DATA


class PublicFiles(StaticFiles):
    async def get_response(self, path, scope):
        if any('.env' in part.lower() or part.startswith('.') for part in Path(path).parts):
            raise HTTPException(404, 'Not found')
        return await super().get_response(path, scope)


def create_app(web_root=None, data_root=None):
    web_root = Path(web_root) if web_root else ROOT / 'web/dist'
    data_root = Path(data_root) if data_root else WEB_DATA

    @asynccontextmanager
    async def lifespan(app):
        async with api.router.lifespan_context(api):
            yield

    app = FastAPI(title='Otec', docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount('/api', api)
    app.mount('/data', PublicFiles(directory=data_root, check_dir=False))
    app.mount('/assets/vehicles', PublicFiles(directory=ROOT/'web/public/assets/vehicles', check_dir=False))
    app.mount('/assets', PublicFiles(directory=web_root/'assets', check_dir=False))

    @app.get('/{path:path}')
    def viewer(path: str):
        # Missing JSON/binary assets must never become a misleading HTML 200.
        if path and ('.' in path or path.split('/')[0] in ('api', 'data', 'assets')):
            raise HTTPException(404, 'Not found')
        page = web_root/'index.html'
        if not page.is_file():
            raise HTTPException(503, 'Viewer build unavailable. Start with make run.')
        return FileResponse(page, headers={'Cache-Control':'no-cache'})

    return app


app = create_app()
