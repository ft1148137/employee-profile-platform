import asyncio

from app.core.db import engine
from app.domains.im.gateway import Gateway

if __name__ == "__main__":
    asyncio.run(Gateway(engine).run())
