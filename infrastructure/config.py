from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    DB_DRIVER: str = "postgresql+psycopg"
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 55432
    DB_NAME: str = "app"
    DB_USER: str = "app"
    DB_PASSWORD: str

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername=self.DB_DRIVER,
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        )


settings = Settings()
