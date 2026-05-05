from datetime import date, datetime

from sqlalchemy import Boolean, Date, Integer, JSON, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str | None] = mapped_column(String(20))

    # Multiple occupations / trades the candidate can do
    skills: Mapped[list | None] = mapped_column(JSON, default=list)
    # Primary desired occupation (kept for backward compat with voice commands)
    desired_occupation: Mapped[str] = mapped_column(String(255), nullable=False)

    place_of_birth: Mapped[str] = mapped_column(String(255), nullable=False)
    current_city: Mapped[str] = mapped_column(String(255), nullable=False)
    current_area: Mapped[str] = mapped_column(String(255), nullable=False)

    languages: Mapped[list | None] = mapped_column(JSON, default=list)
    experience_years: Mapped[int | None] = mapped_column(Integer)

    desired_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    desired_salary_min: Mapped[float | None] = mapped_column(Numeric(12, 2))
    desired_salary_max: Mapped[float | None] = mapped_column(Numeric(12, 2))

    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_complete: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        default=func.now(), onupdate=func.now()
    )
