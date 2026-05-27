from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ping_db(session: AsyncSession) -> bool:
    await session.execute(text('SELECT 1'))
    return True
