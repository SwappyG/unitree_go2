import asyncio
import sys
from arcana_go2.arcana_go2 import ArcanaGO2
from arcana_go2.logger import LogLevel

async def main():
    async with ArcanaGO2(base_url="http://localhost:5656") as go2:
        await go2.sit(id=0, priority=0)
    

if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
    