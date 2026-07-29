from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "api-gateway"
    port: int = 8000
    log_level: str = "info"
    postgres_url: str = "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"
    redis_url: str = "redis://redis:6379/0"
    neo4j_url: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "nquark"
    qdrant_url: str = "http://qdrant:6333"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "nquark"
    minio_secret_key: str = "nquark"


settings = Settings()
