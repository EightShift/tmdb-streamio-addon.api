from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
import tmdb
from i18n import i18n
from routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with tmdb.open_session():
        await tmdb.registry.load_genres()
        await tmdb.registry.load_collections()
        await tmdb.registry.ensure(i18n.default_language)
        yield


app = FastAPI(title=config.NAME, version=config.VERSION, lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)
