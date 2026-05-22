from pydantic_settings import BaseSettings, SettingsConfigDict


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
    admin_messenger_psid: str = ""  # set to notify admin on Messenger when escalation occurs
    environment: str = "development"
    dev_username: str = "honđa"
    dev_password: str = "spiritstone2025"

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
