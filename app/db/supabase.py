from supabase import acreate_client, AsyncClient
from app.config import settings

_client: AsyncClient | None = None


async def get_client() -> AsyncClient:
    """Return async Supabase client using service_role key to bypass RLS."""
    global _client
    if _client is None:
        key = settings.supabase_service_key or settings.supabase_key
        _client = await acreate_client(settings.supabase_url, key)
    return _client
