"""Organization repositories: companies / departments / employees."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import Company, Department, Employee


def get_default_company(db: Session) -> Company | None:
    return db.scalars(select(Company).order_by(Company.id).limit(1)).first()


def get_company_by_slug(db: Session, slug: str) -> Company | None:
    return db.scalars(select(Company).where(Company.slug == slug)).first()


def get_department_by_slug(db: Session, company_id: int, slug: str) -> Department | None:
    return db.scalars(
        select(Department).where(Department.company_id == company_id, Department.slug == slug)
    ).first()


def list_employees(db: Session, company_id: int | None = None) -> list[Employee]:
    stmt = select(Employee).order_by(Employee.id)
    if company_id is not None:
        stmt = stmt.where(Employee.company_id == company_id)
    return list(db.scalars(stmt))


def get_employee(db: Session, employee_id: int) -> Employee | None:
    return db.get(Employee, employee_id)


def get_employee_by_slug(db: Session, slug: str) -> Employee | None:
    return db.scalars(select(Employee).where(Employee.slug == slug)).first()


def get_employee_by_role(db: Session, company_id: int, role: str) -> Employee | None:
    return db.scalars(
        select(Employee)
        .where(Employee.company_id == company_id, Employee.role == role)
        .order_by(Employee.id)
        .limit(1)
    ).first()


def create_employee(db: Session, **fields) -> Employee:
    employee = Employee(**fields)
    db.add(employee)
    db.flush()
    return employee
