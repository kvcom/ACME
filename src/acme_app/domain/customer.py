from dataclasses import dataclass


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    tier: str
    industry: str
    region: str
    customer_timezone: str
    account_owner: str | None = None
    status: str = 'active'

    @property
    def is_high_value(self) -> bool:
        return self.tier in ('Enterprise', 'Strategic')
