from supabase import create_client, Client
from app.config import settings

_client: Client | None = None


def get_client() -> Client:
    """Return a Supabase client using service_role key to bypass RLS."""
    global _client
    if _client is None:
        # Service role key bypasses RLS — safe for server-side code only
        key = settings.supabase_service_key or settings.supabase_key
        _client = create_client(settings.supabase_url, key)
    return _client
