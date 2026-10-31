"""
Proxy resolver and health checker for Iran network environments.
"""
import logging
from typing import Optional
from urllib.parse import urlparse
import aiohttp
from aiohttp_socks import ProxyConnector

from src.bot.config import settings

logger = logging.getLogger(__name__)


async def check_proxy_health(proxy_url: str, timeout: float = 5.0) -> bool:
    """Test if a proxy is alive and can connect to an external server."""
    if not proxy_url:
        return False
    try:
        connector = ProxyConnector.from_url(proxy_url)
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get("http://connectivitycheck.gstatic.com/generate_204") as response:
                return response.status in (200, 204)
    except Exception as e:
        logger.debug(f"Proxy check failed for {proxy_url}: {e}")
        return False


async def resolve_active_proxy() -> Optional[str]:
    """Select the first functional proxy among primary, fallback, or direct."""
    candidates = [p for p in [settings.proxy_url, settings.proxy_fallback_url] if p]
    for p in candidates:
        if await check_proxy_health(p):
            logger.info(f"Active proxy resolved: {p}")
            return p
    logger.warning("No functional proxy found; falling back to direct connection.")
    return None


def get_connector(proxy_url: Optional[str]) -> Optional[aiohttp.BaseConnector]:
    """Create appropriate aiohttp connector for socks/http proxy or standard TCP."""
    if not proxy_url:
        return aiohttp.TCPConnector(ssl=False)
    parsed = urlparse(proxy_url)
    if parsed.scheme.startswith("socks") or parsed.scheme.startswith("http"):
        return ProxyConnector.from_url(proxy_url, ssl=False)
    return aiohttp.TCPConnector(ssl=False)
