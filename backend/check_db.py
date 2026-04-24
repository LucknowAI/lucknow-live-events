import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath("/home/mrstark/Documents/Repos/Lucknow-events/backend"))

from api.core.database import SessionLocal
from api.models.event import Event
from api.models.raw_event import RawEvent
from sqlalchemy import select

async def check():
    async with SessionLocal() as db:
        events = (await db.execute(select(Event).limit(10))).scalars().all()
        raw_events = (await db.execute(select(RawEvent).limit(10))).scalars().all()
        print('Events:', len(events))
        for e in events:
            print(f' - {e.title} (tba: {e.date_tba}, published: {e.published_at is not None})')
        print('\nRawEvents:', len(raw_events))
        for r in raw_events:
            print(f' - {r.id} : {r.pipeline_status} : {r.reason}')

asyncio.run(check())
