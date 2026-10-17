from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = ""

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_provider: str = "auto"
    gigachat_client_id: str = ""
    gigachat_client_secret: str = ""
    gigachat_model: str = "GigaChat-Pro"

    aviasales_marker: str = ""
    ostrovok_partner_id: str = ""

    travelpayouts_token: str = ""

    payments_provider_token: str = ""
    price_per_trip_rub: int = 690

    cache_dir: str = ""

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key or (self.gigachat_client_id and self.gigachat_client_secret))

    @property
    def payments_enabled(self) -> bool:
        return bool(self.payments_provider_token)


settings = Settings()
