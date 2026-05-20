from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openrouter_api_key: str
    supabase_url: str
    supabase_key: str
    supabase_service_key: str = ""
    messenger_verify_token: str = "placeholder"
    messenger_page_token: str = "placeholder"
    messenger_app_secret: str = ""
    sentry_dsn: str = ""
    cost_cap_per_user_day: float = 0.50
    rate_limit_per_minute: int = 20
    admin_api_key: str = ""
    environment: str = "development"
    dev_username: str = "honđa"
    dev_password: str = "spiritstone2025"

    class Config:
        env_file = ".env"


settings = Settings()
