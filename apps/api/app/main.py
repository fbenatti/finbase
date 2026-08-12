from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base, async_session
from app.routers import connections, etl, rules, periods, analysis, destinations, schema
from app.seed import seed
from app.services import scheduler_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        from app.models.source_connection import SourceConnection  # noqa: F401
        from app.models.ingested_blob import IngestedBlob  # noqa: F401
        from app.models.rules import EtlMapping, BusinessRule, ClassificationRule  # noqa: F401
        from app.models.period_status import PeriodStatus  # noqa: F401
        from app.models.etl_task import EtlTask  # noqa: F401
        from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping  # noqa: F401
        from app.models.skipped_blob import SkippedBlob  # noqa: F401
        from app.models.scheduled_task import ScheduledTask  # noqa: F401
        from app.models.pivot_view import PivotView  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as db:
        await seed(db)

    # Start the ETL scheduler
    await scheduler_service.start_scheduler()

    yield

    # Stop the scheduler
    await scheduler_service.stop_scheduler()


app = FastAPI(title="FinOps ETL Platform", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(connections.router)
app.include_router(etl.router)
app.include_router(rules.router)
app.include_router(periods.router)
app.include_router(analysis.router)
app.include_router(destinations.router)
app.include_router(schema.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
