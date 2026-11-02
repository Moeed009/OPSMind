
import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


async def main():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is missing from .env")

    engine = create_async_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"ssl": "require"},
    )

    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("""
                    SELECT
                        version(),
                        current_database(),
                        current_user
                """)
            )

            print("Supabase connection successful!")
            print(result.fetchone())

    except Exception as exc:
        print("Database connection failed!")
        print(type(exc).__name__, str(exc))

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
