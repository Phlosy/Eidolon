"""Runtime repositories (v0.2): runtime_instances / runtime_images / employee_brains."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.brain.traits import BrainTraits
from app.core.request_context import get_request_identity
from app.models.organization import Employee
from app.models.runtime import EmployeeBrain, RuntimeImage, RuntimeInstance
from app.repositories import persons as person_repo

# ---- runtime instances ----


def list_instances(db: Session) -> list[RuntimeInstance]:
    stmt = select(RuntimeInstance).order_by(RuntimeInstance.id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Employee, RuntimeInstance.employee_id == Employee.id).where(
            Employee.company_id == identity.company_id
        )
    return list(db.scalars(stmt))


def get_instance(db: Session, instance_id: int) -> RuntimeInstance | None:
    stmt = select(RuntimeInstance).where(RuntimeInstance.id == instance_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Employee, RuntimeInstance.employee_id == Employee.id).where(
            Employee.company_id == identity.company_id
        )
    return db.scalar(stmt)


def get_instance_for_employee(db: Session, employee_id: int) -> RuntimeInstance | None:
    # R1.4：属主口径切 person_id（单一入口换算，带旧口径回落）；
    # 公司隔离 join 仍走 employees 成员身份（persons 无 company_id，见批次 2 裁定）。
    stmt = select(RuntimeInstance).where(
        person_repo.read_criterion(
            db, employee_id, RuntimeInstance.person_id, RuntimeInstance.employee_id
        )
    )
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Employee, RuntimeInstance.employee_id == Employee.id).where(
            Employee.company_id == identity.company_id
        )
    return db.scalars(stmt).first()


def list_instances_by_status(db: Session, statuses: list[str]) -> list[RuntimeInstance]:
    return list(db.scalars(select(RuntimeInstance).where(RuntimeInstance.status.in_(statuses))))


def list_instances_by_runtime_type(db: Session, runtime_type: str) -> list[RuntimeInstance]:
    return list(
        db.scalars(select(RuntimeInstance).where(RuntimeInstance.runtime_type == runtime_type))
    )


def create_instance(db: Session, **fields) -> RuntimeInstance:
    # 双写（R1.4）：employee_id（deprecated 镜像）+ person_id（权威口径）
    fields.setdefault("person_id", person_repo.write_person_id(db, fields["employee_id"]))
    instance = RuntimeInstance(**fields)
    db.add(instance)
    db.flush()
    return instance


def delete_instance(db: Session, instance: RuntimeInstance) -> None:
    db.delete(instance)
    db.flush()


# ---- runtime images ----


def list_images(db: Session) -> list[RuntimeImage]:
    return list(db.scalars(select(RuntimeImage).order_by(RuntimeImage.id)))


def get_image(db: Session, runtime_type: str) -> RuntimeImage | None:
    return db.scalars(select(RuntimeImage).where(RuntimeImage.runtime_type == runtime_type)).first()


def upsert_image(db: Session, runtime_type: str, **fields) -> RuntimeImage:
    image = get_image(db, runtime_type)
    if image is None:
        image = RuntimeImage(runtime_type=runtime_type, **fields)
        db.add(image)
    else:
        for key, value in fields.items():
            setattr(image, key, value)
    db.flush()
    return image


def count_instances_using(db: Session, runtime_type: str) -> int:
    return int(
        db.scalar(
            select(func.count(RuntimeInstance.id)).where(
                RuntimeInstance.runtime_type == runtime_type
            )
        )
        or 0
    )


# ---- employee brains ----
#
# R1.1 切读（docs/person-core-migration.md D4 批次 1）：brain 跟人不跟成员身份，
# 读口径 = person_id（入参仍是 employee_id，经 person_repo 单一入口换算+回落）。


def get_brain(db: Session, employee_id: int) -> EmployeeBrain | None:
    return db.scalars(
        select(EmployeeBrain).where(
            person_repo.read_criterion(
                db, employee_id, EmployeeBrain.person_id, EmployeeBrain.employee_id
            )
        )
    ).first()


def get_brain_by_person(db: Session, person_id: int) -> EmployeeBrain | None:
    """person 口径直读（T1.2：培养期角色没有 employee_id，走不了上面的换算入口）。"""
    return db.scalars(select(EmployeeBrain).where(EmployeeBrain.person_id == person_id)).first()


def ensure_brain(db: Session, employee_id: int, **defaults) -> EmployeeBrain:
    brain = get_brain(db, employee_id)
    if brain is None:
        # 双写：employee_id（deprecated 镜像）+ person_id（权威口径）
        brain = EmployeeBrain(
            employee_id=employee_id,
            person_id=person_repo.write_person_id(db, employee_id),
            **defaults,
        )
        # 新 brain 创建时就拥有 traits（唯一权威）；legacy 镜像列继续存在以便回滚读旧值（§4.1）。
        brain.traits = BrainTraits.from_brain(brain).to_json()
        db.add(brain)
        db.flush()
    return brain
