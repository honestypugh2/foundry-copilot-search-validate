"""
Pydantic models for the HR Policy Knowledge Lab API.
"""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Incoming user query."""
    query: str = Field(..., description="Natural-language HR policy question")


class Reference(BaseModel):
    """A grounded citation reference."""
    filePath: str
    documentType: str


class Match(BaseModel):
    """A single search match from the retrieval agent."""
    content: str
    filePath: str
    documentType: str
    container: str


class QueryResponse(BaseModel):
    """Response from the sequential orchestrator."""
    status: str
    user_query: str
    matches: list[Match]
    references: list[Reference]
    is_grounded: bool
