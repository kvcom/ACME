from pydantic import BaseModel, Field


class CustomerSearchInput(BaseModel):
    customer_name: str = Field(min_length=1, max_length=100)
