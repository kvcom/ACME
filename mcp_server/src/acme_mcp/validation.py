from pydantic import BaseModel


def validate_input(model: type[BaseModel], payload: dict) -> BaseModel:
    return model.model_validate(payload)
