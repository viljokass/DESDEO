"""Defines models for archiving solutions."""

from typing import TYPE_CHECKING

from sqlmodel import JSON, Column, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .problem import ProblemDB
    from .state import StateDB
    from .user import User

class SolutionAddress(SQLModel):
    objective_values: dict[str, float] = Field(sa_column=Column(JSON))
    address_state: int = Field(sa_column=Column(JSON))
    address_result: int = Field(sa_column=Column(JSON))

class UserSavedSolutionDB(SolutionAddress, table=True):
    """Database model of an archive entry."""

    id: int | None = Field(primary_key=True, default=None)
    name: str | None = Field(default=None, nullable=True)  # Optional name for the solution
    user_id: int | None = Field(foreign_key="user.id", default=None)
    problem_id: int | None = Field(foreign_key="problemdb.id", default=None)
    state_id: int | None = Field(foreign_key="statedb.id", default=None) # The save state, not the state the solution is found from?
    # Back populates
    user: "User" = Relationship(back_populates="archive")
    problem: "ProblemDB" = Relationship(back_populates="solutions")
    state: "StateDB" = Relationship(back_populates="saved_solutions")

class UserSavedSolutionAddress(SolutionAddress):
    """Defines a schema for storing archived solutions."""
    name: str | None = Field(
        description="An optional name for the solution, useful for archiving purposes.", default=None
    )

    def to_solution_address(self) -> SolutionAddress:
        """Convert UserSavedSolutionAddress to just SolutionAddress"""
        return SolutionAddress(
            objective_values=self.objective_values,
            address_state=self.address_state,
            address_result=self.address_result
        )
