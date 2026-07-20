"""Pydantic response schemas exposed by the HTTP adapter."""

from pydantic import BaseModel


class TicketCountResponse(BaseModel):
    """Number of tickets in the Atlas store."""

    count: int
