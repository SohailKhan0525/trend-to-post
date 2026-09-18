import logging
from pathlib import Path
from twikit import Client
from .models import Trend

log = logging.getLogger(__name__)

class XTrendClient:
    def __init__(self, username, email, password, cookies_file: Path):
        self.username = username
        self.email = email
        self.password = password
        self.cookies_file = cookies_file
        self.client = Client('en-US')

    async def _login(self):
        self.cookies_file.parent.mkdir(parents=True, exist_ok=True)
        if self.cookies_file.exists():
            self.client.load_cookies(str(self.cookies_file))
            return
        await self.client.login(auth_info_1=self.username, auth_info_2=self.email, password=self.password)
        self.client.save_cookies(str(self.cookies_file))

    async def get_trends(self, limit):
        await self._login()
        raw = await self.client.get_trends('trending')
        result = []
        for rank, item in enumerate(raw[:limit], 1):
            name = getattr(item, 'name', None) or str(item)
            result.append(Trend(name=name.strip(), rank=rank))
        log.info('Collected %d X trends', len(result))
        return result
