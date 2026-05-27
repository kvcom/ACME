from pydantic import BaseModel


class ToolResponse(BaseModel):
    data: dict
