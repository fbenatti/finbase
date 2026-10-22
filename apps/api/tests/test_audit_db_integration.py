"""Integração: o middleware de auditoria grava de fato no Postgres.

Cobre PUT (antes/depois/diff), DELETE (antes, sem depois) e falha 4xx
(action_label="failed"). Roda contra TEST_DATABASE_URL, que precisa ter a
tabela audit_log migrada (ex.: postgresql+asyncpg://finops:finops@localhost:5434/finops).
Sem a variável ou sem conexão, o teste é pulado.

Cria uma tabela temporária audit_test_widget e remove os próprios eventos ao final.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL não definida")


def _scenario_factory():
    from fastapi import FastAPI, HTTPException
    from sqlalchemy import Column, Integer, String, select, delete, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import declarative_base

    import app.core.audit_middleware as mw
    from app.models.audit_log import AuditLog

    TestBase = declarative_base()

    class AuditWidget(TestBase):
        __tablename__ = "audit_test_widget"
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String(100), nullable=False)
        password = Column(String(100))

    async def scenario():
        engine = create_async_engine(TEST_DB_URL)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1 FROM audit_log LIMIT 1"))
                await conn.run_sync(TestBase.metadata.drop_all)
                await conn.run_sync(TestBase.metadata.create_all)
        except OSError as exc:
            await engine.dispose()
            pytest.skip(f"Banco de teste indisponível: {exc}")
        except Exception as exc:  # audit_log ausente ou conexão recusada
            await engine.dispose()
            pytest.skip(f"Banco de teste não utilizável: {exc}")

        Session = async_sessionmaker(engine, expire_on_commit=False)
        mw.async_session = Session
        mw.AUDIT_MODELS["widgets"] = AuditWidget

        async with Session() as db:
            # sobras de uma execução anterior que tenha falhado antes da limpeza
            await db.execute(delete(AuditLog).where(AuditLog.path.like("/widgets%")))
            db.add(AuditWidget(id=1, name="antigo", password="segredo"))
            db.add(AuditWidget(id=2, name="para apagar"))
            await db.commit()

        app = FastAPI()
        app.add_middleware(mw.AuditCaptureMiddleware)

        @app.put("/widgets/{wid}")
        async def put_widget(wid: int, payload: dict):
            async with Session() as db:
                row = await db.get(AuditWidget, wid)
                if row is None:
                    raise HTTPException(status_code=404, detail="não encontrado")
                row.name = payload["name"]
                await db.commit()
            return {"ok": True}

        @app.delete("/widgets/{wid}", status_code=204)
        async def delete_widget(wid: int):
            async with Session() as db:
                await db.execute(delete(AuditWidget).where(AuditWidget.id == wid))
                await db.commit()

        @app.post("/widgets/broken")
        async def broken():
            raise HTTPException(status_code=400, detail="inválido")

        import httpx
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r_put = await client.put("/widgets/1", json={"name": "novo", "password": "x"})
            r_del = await client.delete("/widgets/2")
            r_bad = await client.post("/widgets/broken", json={})
        assert r_put.status_code == 200
        assert r_del.status_code == 204
        assert r_bad.status_code == 400

        # negação do RBAC é respondida antes da rota: gravada pelo caminho dedicado
        from app.core.audit_middleware import record_denied_attempt
        await record_denied_attempt(
            {"type": "http", "method": "PUT", "path": "/widgets/1", "headers": [], "client": ("10.0.0.1", 1)},
            403,
        )

        async with Session() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.path.like("/widgets%")).order_by(AuditLog.id)
            )).scalars().all()
            by_action = {r.action_label: r for r in rows}

            put_row = by_action["update"]
            assert put_row.module == "widgets"
            assert put_row.entity_id == "1"
            assert put_row.status_code == 200
            assert put_row.before_json["name"] == "antigo"
            assert put_row.after_json["name"] == "novo"
            assert put_row.before_json["password"] == "***"
            changed = {c["field"] for c in (put_row.changes_json or [])}
            assert "name" in changed
            assert "password" not in changed

            del_row = by_action["delete"]
            assert del_row.entity_id == "2"
            assert del_row.before_json["name"] == "para apagar"
            assert del_row.after_json is None
            assert del_row.changes_json is None

            failed_row = next(r for r in rows if r.action_label == "failed" and r.status_code == 400)
            assert failed_row.path == "/widgets/broken"

            denied = (await db.execute(
                select(AuditLog).where(AuditLog.path == "/widgets/1", AuditLog.status_code == 403)
            )).scalar_one()
            assert denied.action_label == "failed"
            assert denied.entity_id == "1"
            assert denied.after_json is None

            await db.execute(delete(AuditLog).where(AuditLog.path.like("/widgets%")))
            await db.commit()

        async with engine.begin() as conn:
            await conn.run_sync(TestBase.metadata.drop_all)
        await engine.dispose()
        mw.AUDIT_MODELS.pop("widgets", None)

    return scenario


def test_audit_middleware_writes_update_delete_and_failures():
    scenario = _scenario_factory()
    asyncio.run(scenario())
