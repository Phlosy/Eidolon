"""Organization repositories: companies / departments / employees."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.organization import Company, Department, Employee


def get_default_company(db: Session) -> Company | None:
    identity = get_request_identity()
    if identity is not None:
        return db.get(Company, identity.company_id)
    return db.scalars(select(Company).order_by(Company.id).limit(1)).first()


def get_company_by_slug(db: Session, slug: str) -> Company | None:
    return db.scalars(select(Company).where(Company.slug == slug)).first()


def get_department_by_slug(db: Session, company_id: int, slug: str) -> Department | None:
    return db.scalars(
        select(Department).where(Department.company_id == company_id, Department.slug == slug)
    ).first()


def list_employees(db: Session, company_id: int | None = None) -> list[Employee]:
    identity = get_request_identity()
    if company_id is None and identity is not None:
        company_id = identity.company_id
    stmt = select(Employee).order_by(Employee.id)
    if company_id is not None:
        stmt = stmt.where(Employee.company_id == company_id)
    return list(db.scalars(stmt))


def get_employee(db: Session, employee_id: int) -> Employee | None:
    employee = db.get(Employee, employee_id)
    identity = get_request_identity()
    if employee is not None and identity is not None and employee.company_id != identity.company_id:
        return None
    return employee


def get_employee_by_slug(db: Session, slug: str) -> Employee | None:
    stmt = select(Employee).where(Employee.slug == slug)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(Employee.company_id == identity.company_id)
    return db.scalars(stmt).first()


def slug_taken_anywhere(db: Session, slug: str) -> bool:
    """`slug` 是否已被**任何公司**占用（唯一性判断专用）。

    不能用上面的 `get_employee_by_slug` 判唯一性：那是按当前请求公司过滤的读接口，
    而 `employees.slug` 及由它派生的 `username` / `workspace_path` / `memory_namespace`
    在库里都是全局唯一列（models/organization.py）。公司内查不到 ≠ 能插入：
    跨公司同名会在 INSERT 上撞成 500。
    """
    return db.scalar(select(Employee.id).where(Employee.slug == slug)) is not None


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
