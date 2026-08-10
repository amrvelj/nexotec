"""Shared Pydantic config: JSON bodies are camelCase, ORM/Python stays
snake_case, translated at this boundary (API-conventions cross-cutting rule).
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
