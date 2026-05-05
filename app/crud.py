"""CRUD operations for the Candidate table."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Candidate


def create_candidate(
    db: Session,
    *,
    name: str,
    phone: str | None = None,
    age: int,
    gender: str | None = None,
    skills: list[str] | None = None,
    desired_occupation: str,
    place_of_birth: str,
    current_city: str,
    current_area: str,
    languages: list[str] | None = None,
    experience_years: int | None = None,
    desired_start_date: str | date,
    desired_salary_min: float | None = None,
    desired_salary_max: float | None = None,
    # Accept legacy single salary field and map it to min/max
    desired_salary: float | None = None,
) -> Candidate:
    """Insert a new candidate row."""
    if isinstance(desired_start_date, str):
        desired_start_date = date.fromisoformat(desired_start_date)

    # Backward compat: if caller sends the old single salary field, use it for both min and max
    if desired_salary is not None and desired_salary_min is None and desired_salary_max is None:
        desired_salary_min = desired_salary
        desired_salary_max = desired_salary

    candidate = Candidate(
        name=name,
        phone=phone,
        age=age,
        gender=gender,
        skills=skills or [],
        desired_occupation=desired_occupation,
        place_of_birth=place_of_birth,
        current_city=current_city,
        current_area=current_area,
        languages=languages or [],
        experience_years=experience_years,
        desired_start_date=desired_start_date,
        desired_salary_min=desired_salary_min,
        desired_salary_max=desired_salary_max,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def get_candidate(
    db: Session,
    *,
    id: int | None = None,
    name: str | None = None,
    desired_occupation: str | None = None,
    current_city: str | None = None,
    current_area: str | None = None,
    desired_start_date: str | date | None = None,
    desired_salary: float | None = None,
    desired_salary_min: float | None = None,
    desired_salary_max: float | None = None,
    experience_years: int | None = None,
    gender: str | None = None,
) -> list[Candidate]:
    """Look up candidates with flexible filters."""
    stmt = select(Candidate)
    if id is not None:
        stmt = stmt.where(Candidate.id == id)
    if name is not None:
        stmt = stmt.where(Candidate.name.ilike(f"%{name}%"))
    if desired_occupation is not None:
        stmt = stmt.where(Candidate.desired_occupation.ilike(f"%{desired_occupation}%"))
    if current_city is not None:
        stmt = stmt.where(Candidate.current_city.ilike(f"%{current_city}%"))
    if current_area is not None:
        stmt = stmt.where(Candidate.current_area.ilike(f"%{current_area}%"))
    if desired_start_date is not None:
        if isinstance(desired_start_date, str):
            desired_start_date = date.fromisoformat(desired_start_date)
        stmt = stmt.where(Candidate.desired_start_date == desired_start_date)
    if gender is not None:
        stmt = stmt.where(Candidate.gender.ilike(gender))
    if experience_years is not None:
        stmt = stmt.where(Candidate.experience_years >= experience_years)
    # Legacy single salary: treat as max budget — find candidates whose min is at or below it
    if desired_salary is not None and desired_salary_min is None:
        stmt = stmt.where(Candidate.desired_salary_min <= desired_salary)
    # Salary range filters
    if desired_salary_min is not None:
        stmt = stmt.where(Candidate.desired_salary_max >= desired_salary_min)
    if desired_salary_max is not None:
        stmt = stmt.where(Candidate.desired_salary_min <= desired_salary_max)
    return list(db.execute(stmt).scalars().all())


def update_candidate(
    db: Session,
    *,
    id: int,
    name: str | None = None,
    phone: str | None = None,
    age: int | None = None,
    gender: str | None = None,
    skills: list[str] | None = None,
    desired_occupation: str | None = None,
    place_of_birth: str | None = None,
    current_city: str | None = None,
    current_area: str | None = None,
    languages: list[str] | None = None,
    experience_years: int | None = None,
    desired_start_date: str | date | None = None,
    desired_salary_min: float | None = None,
    desired_salary_max: float | None = None,
    desired_salary: float | None = None,
) -> Candidate:
    """Update fields on an existing candidate. Raises ValueError if not found."""
    candidate = db.get(Candidate, id)
    if candidate is None:
        raise ValueError(f"Candidate with id {id} not found")
    if name is not None:
        candidate.name = name
    if phone is not None:
        candidate.phone = phone
    if age is not None:
        candidate.age = age
    if gender is not None:
        candidate.gender = gender
    if skills is not None:
        candidate.skills = skills
    if desired_occupation is not None:
        candidate.desired_occupation = desired_occupation
    if place_of_birth is not None:
        candidate.place_of_birth = place_of_birth
    if current_city is not None:
        candidate.current_city = current_city
    if current_area is not None:
        candidate.current_area = current_area
    if languages is not None:
        candidate.languages = languages
    if experience_years is not None:
        candidate.experience_years = experience_years
    if desired_start_date is not None:
        if isinstance(desired_start_date, str):
            desired_start_date = date.fromisoformat(desired_start_date)
        candidate.desired_start_date = desired_start_date
    # Backward compat: single salary → both min and max
    if desired_salary is not None and desired_salary_min is None and desired_salary_max is None:
        candidate.desired_salary_min = desired_salary
        candidate.desired_salary_max = desired_salary
    if desired_salary_min is not None:
        candidate.desired_salary_min = desired_salary_min
    if desired_salary_max is not None:
        candidate.desired_salary_max = desired_salary_max
    db.commit()
    db.refresh(candidate)
    return candidate


def delete_candidate(db: Session, *, id: int) -> bool:
    """Delete a candidate by id. Returns True if deleted, False if not found."""
    candidate = db.get(Candidate, id)
    if candidate is None:
        return False
    db.delete(candidate)
    db.commit()
    return True


OPERATION_MAP = {
    "create_candidate": create_candidate,
    "get_candidate": get_candidate,
    "update_candidate": update_candidate,
    "delete_candidate": delete_candidate,
}
