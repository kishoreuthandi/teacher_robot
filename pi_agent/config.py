from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    left_in1: int = 17
    left_in2: int = 27
    left_pwm: int = 12
    right_in1: int = 22
    right_in2: int = 23
    right_pwm: int = 13
    default_speed: float = 0.65
    camera_index: int = 0


settings = Settings()

