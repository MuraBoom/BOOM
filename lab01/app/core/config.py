from pydantic_settings import (
    BaseSettings,
)


class Settings(BaseSettings):
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str

    @property
    def postgres_url(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    MONGO_HOST: str = "localhost"
    MONGO_PORT: int = 27017
    MONGO_INITDB_ROOT_USERNAME: str
    MONGO_INITDB_ROOT_PASSWORD: str

    @property
    def mongo_url(self) -> str:
        return f"mongodb://{self.MONGO_INITDB_ROOT_USERNAME}:{self.MONGO_INITDB_ROOT_PASSWORD}@{self.MONGO_HOST}:{self.MONGO_PORT}/"

    NEO4J_HOST: str = "localhost"
    NEO4J_BOLT_PORT: int = 7687
    # login/password format
    NEO4J_AUTH: str

    @property
    def neo4j_url(self) -> str:
        user, password = self.NEO4J_AUTH.split("/")
        return f"bolt://{user}:{password}@{self.NEO4J_HOST}:{self.NEO4J_BOLT_PORT}"

    OPENSEARCH_HOST: str = "localhost"
    OPENSEARCH_PORT: int = 9200
    OPENSEARCH_INITIAL_ADMIN_PASSWORD: str

    @property
    def opensearch_url(self) -> str:
        return f"https://admin:{self.OPENSEARCH_INITIAL_ADMIN_PASSWORD}@{self.OPENSEARCH_HOST}:{self.OPENSEARCH_PORT}"


settings = Settings()
