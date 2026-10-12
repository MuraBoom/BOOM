import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from neo4j import AsyncGraphDatabase
from starlette.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def _neo4j_auth():
    raw = settings.NEO4J_AUTH
    if raw.lower() == "none" or "/" not in raw:
        return None
    user, password = raw.split("/", 1)
    return (user, password)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Connecting to databases...")

    pg_pool = await asyncpg.create_pool(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database=settings.POSTGRES_DB,
        min_size=2,
        max_size=10,
    )

    neo4j_driver = AsyncGraphDatabase.driver(
        f"bolt://{settings.NEO4J_HOST}:{settings.NEO4J_BOLT_PORT}",
        auth=_neo4j_auth(),
    )

    app.state.pg_pool = pg_pool
    app.state.neo4j = neo4j_driver

    logger.info("Database connections established.")
    yield

    logger.info("Closing database connections...")
    await pg_pool.close()
    await neo4j_driver.close()
    logger.info("All connections closed.")


app = FastAPI(root_path="/lab02", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
