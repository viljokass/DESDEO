""" Defines end-points to access functionalities related to the NIMBUS method."""

from typing import Annotated
from numpy import allclose

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, or_, select

from desdeo.api.db import get_session
from desdeo.api.models import (
    InteractiveSessionDB,
    NIMBUSClassificationRequest,
    NIMBUSClassificationState,
    NIMBUSInitializationRequest,
    NIMBUSInitializationState,
    NIMBUSSaveRequest,
    NIMBUSSaveState,
    NIMBUSClassificationResponse,
    NIMBUSSaveResponse,
    PreferenceDB,
    ProblemDB,
    StateDB,
    User,
    UserSavedSolutionDB,
    SolutionAddress,
)
from desdeo.api.routers.user_authentication import get_current_user
from desdeo.api.utils.database import user_save_solutions
from desdeo.mcdm.nimbus import generate_starting_point, solve_sub_problems
from desdeo.problem import Problem
from desdeo.tools import SolverResults

router = APIRouter(prefix="/method/nimbus")

def filter_duplicates(
    solutions: list[SolutionAddress]
) -> list[SolutionAddress]:
    """Filters out the duplicate values of objectives."""

    # No solutions or only one solution. There can not be any duplicates.
    if len(solutions) < 2:
        return solutions

    # Get the objective values
    objective_values_list = list(map(lambda sol: sol.objective_values, solutions))
    # Get the function symbols
    objective_keys = [key for key in objective_values_list[0]]
    # Get the corresponding values for functions into a list of lists of values
    valuelists = list(map(lambda dictionary: list(map(lambda key: dictionary[key], objective_keys)), objective_values_list))

    # Check duplicate indices
    duplicate_indices = []
    for i in range(len(solutions) - 1):
        for j in range(i + 1, len(solutions)):
            # If all values of the objective functions are (nearly) identical, that's a duplicate
            if allclose(valuelists[i], valuelists[j]):
                duplicate_indices.append(i)
    
    # Quite the memory hell. See If there's a smarter way to do this
    new_solutions = []
    for i in range(len(solutions)):
        if i not in duplicate_indices:
            new_solutions.append(solutions[i])

    return new_solutions

@router.post("/solve")
def solve_solutions(
    request: NIMBUSClassificationRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> NIMBUSClassificationResponse:
    """Solve the problem using the NIMBUS method."""
    if request.session_id is not None:
        statement = select(InteractiveSessionDB).where(InteractiveSessionDB.id == request.session_id)
        interactive_session = session.exec(statement)

        if interactive_session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Could not find interactive session with id={request.session_id}.",
            )
    else:
        # request.session_id is None:
        # use active session instead
        statement = select(InteractiveSessionDB).where(InteractiveSessionDB.id == user.active_session_id)

        interactive_session = session.exec(statement).first()

    # fetch the problem from the DB
    statement = select(ProblemDB).where(ProblemDB.user_id == user.id, ProblemDB.id == request.problem_id)
    problem_db = session.exec(statement).first()

    if problem_db is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Problem with id={request.problem_id} could not be found."
        )

    problem = Problem.from_problemdb(problem_db)

    solver_results: list[SolverResults] = solve_sub_problems(
        problem=problem,
        current_objectives=request.current_objectives,
        reference_point=request.preference.aspiration_levels,
        num_desired=request.num_desired,
        scalarization_options=request.scalarization_options,
        solver=request.solver,
        solver_options=request.solver_options,
    )
    # create a new preference in the DB
    preference_db = PreferenceDB(user_id=user.id, problem_id=problem_db.id, preference=request.preference)

    session.add(preference_db)
    session.commit()
    session.refresh(preference_db)

    # fetch parent state
    if request.parent_state_id is None:
        # parent state is assumed to be the last state added to the session.
        parent_state = (
            interactive_session.states[-1]
            if (interactive_session is not None and len(interactive_session.states) > 0)
            else None
        )

    else:
        # request.parent_state_id is not None
        statement = select(StateDB).where(StateDB.id == request.parent_state_id)
        parent_state = session.exec(statement).first()

        if parent_state is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Could not find state with id={request.parent_state_id}"
            )

    nimbus_state = NIMBUSClassificationState(
        scalarization_options=request.scalarization_options,
        solver=request.solver,
        solver_options=request.solver_options,
        solver_results=solver_results,
        current_objectives=request.current_objectives,
        num_desired=request.num_desired,
        previous_preference=request.preference,
    )

    # create DB state and add it to the DB
    state = StateDB(
        problem_id=problem_db.id,
        preference_id=preference_db.id,
        session_id=interactive_session.id if interactive_session is not None else None,
        parent_id=parent_state.id if parent_state is not None else None,
        state=nimbus_state,
    )

    session.add(state)
    session.commit()
    session.refresh(state)

    
    # Collect all current solutions
    current_solutions: list[SolutionAddress] = []
    for i in range(len(solver_results)):
        current_solutions.append(
            SolutionAddress(
                objective_values=solver_results[i].optimal_objectives,
                address_state=state.id,
                address_result=i
            )
        )

    # Collect all saved solutions
    saved_solutions: list[SolutionAddress] = []
    saved_from_db = session.exec(select(UserSavedSolutionDB).where(
        UserSavedSolutionDB.problem_id == request.problem_id,
        UserSavedSolutionDB.user_id == user.id
    )).all()
    for saved_solution in saved_from_db:
        saved_solutions.append(
            SolutionAddress(
                objective_values=saved_solution.objective_values,
                address_state=saved_solution.address_state,
                address_result=saved_solution.address_result
            )
        )

    saved_solutions = filter_duplicates(saved_solutions)

    all_solutions: list[SolutionAddress] = []
    parent = state
    while parent != None:
        # Skip over states that are not NIMBUS classification states
        if not (parent.state.method == "nimbus" and parent.state.phase == "solve_candidates"):
            parent = parent.parent
            continue
        # Get the solver results from state
        parent_solver_results: list[SolverResults] = parent.state.solver_results
        for i in range(len(parent_solver_results)):
            all_solutions.append(
                SolutionAddress(
                    objective_values=parent_solver_results[i].optimal_objectives,
                    address_state=parent.id,
                    address_result=i
                )
            )
        parent = parent.parent

    all_solutions = filter_duplicates(all_solutions)

    response = NIMBUSClassificationResponse(
        state_id=state.id,
        previous_preference=request.preference,
        current_solutions=current_solutions,
        saved_solutions=saved_solutions,
        all_solutions=all_solutions
    )

    return response


@router.post("/initialize")
def initialize(
    request: NIMBUSInitializationRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> NIMBUSInitializationState | NIMBUSClassificationState:
    """Initialize the problem for the NIMBUS method."""
    if request.session_id is not None:
        statement = select(InteractiveSessionDB).where(InteractiveSessionDB.id == request.session_id)
        interactive_session = session.exec(statement)

        if interactive_session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Could not find interactive session with id={request.session_id}.",
            )
    else:
        # request.session_id is None:
        # use active session instead
        statement = select(InteractiveSessionDB).where(InteractiveSessionDB.id == user.active_session_id)

        interactive_session = session.exec(statement).first()

    # fetch the problem from the DB
    statement = select(ProblemDB).where(ProblemDB.user_id == user.id, ProblemDB.id == request.problem_id)
    problem_db = session.exec(statement).first()

    if problem_db is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Problem with id={request.problem_id} could not be found."
        )

    problem = Problem.from_problemdb(problem_db)


    # find the latest NIMBUSClassificationState for the problem and session
    statement = (
        select(StateDB)
        .where(
            StateDB.problem_id == problem_db.id,
            StateDB.session_id == (interactive_session.id if interactive_session is not None else None),
            StateDB.state["method"] == "nimbus",
            )
        .order_by(StateDB.id.desc())
    )
    last_statedb = session.exec(statement).first()

    if last_statedb is not None:
        nimbus_state = last_statedb.state
    # if there is no last nimbus state, generate a starting point and create an initialization state
    else:
        start_result = generate_starting_point(
                    problem=problem,
                    solver=request.solver,
                )
        # fetch parent state if it is given
        if request.parent_state_id is None:
            parent_state = None
        else:
            statement = session.select(StateDB).where(StateDB.id == request.parent_state_id)
            parent_state = session.exec(statement).first()

            if parent_state is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Could not find state with id={request.parent_state_id}"
                )

        nimbus_state = NIMBUSInitializationState(
            solver=request.solver,
            solver_results=[start_result],
        )

        # create DB state and add it to the DB
        state = StateDB(
            problem_id=problem_db.id,
            preference_id=None,
            session_id=interactive_session.id if interactive_session is not None else None,
            parent_id=parent_state.id if parent_state is not None else None,
            state=nimbus_state,
        )

        session.add(state)
        session.commit()
        session.refresh(state)

    return nimbus_state

@router.post("/save")
def save(
    request: NIMBUSSaveRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> NIMBUSSaveResponse:
    """Save solutions."""
    if request.session_id is not None:
        statement = select(InteractiveSessionDB).where(InteractiveSessionDB.id == request.session_id)
        interactive_session = session.exec(statement)

        if interactive_session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Could not find interactive session with id={request.session_id}.",
            )
    else:
        # request.session_id is None:
        # use active session instead
        statement = select(InteractiveSessionDB).where(InteractiveSessionDB.id == user.active_session_id)

        interactive_session = session.exec(statement).first()

    # fetch parent state
    if request.parent_state_id is None:
        # parent state is assumed to be the last state added to the session.
        parent_state = (
            interactive_session.states[-1]
            if (interactive_session is not None and len(interactive_session.states) > 0)
            else None
        )

    else:
        # request.parent_state_id is not None
        statement = select(StateDB).where(StateDB.id == request.parent_state_id)
        parent_state = session.exec(statement).first()

        if parent_state is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Could not find state with id={request.parent_state_id}"
            )

    # save solver results for state in SolverResults format just for consistency (dont save name field to state)
    save_state = NIMBUSSaveState(
        solution_addresses=[solution.to_solution_address() for solution in request.solutions]
    )

    # create DB state
    state = StateDB(
        problem_id=request.problem_id,
        session_id=interactive_session.id if interactive_session is not None else None,
        parent_id=parent_state.id if parent_state is not None else None,
        state=save_state,
    )
    # save solutions to the user's archive and add state to the DB
    user_save_solutions(state, request.solutions, user.id, session)

    return NIMBUSSaveResponse(
        state_id = state.id
    )
