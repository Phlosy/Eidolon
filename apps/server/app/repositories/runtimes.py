"""Runtime repositories (v0.2): runtime_instances / runtime_images / employee_brains."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.runtime import EmployeeBrain, RuntimeImage, RuntimeInstance

# ---- runtime instances ----


def list_instances(db: Session) -> list[RuntimeInstance]:
    return list(db.scalars(select(RuntimeInstance).order_by(RuntimeInstance.id)))


def get_instance(db: Session, instance_id: int) -> RuntimeInstance | None:
    return db.get(RuntimeInstance, instance_id)


def get_instance_for_employee(db: Session, employee_id: int) -> RuntimeInstance | None:
    return db.scalars(
        select(RuntimeInstance).where(RuntimeInstance.employee_id == employee_id)
    ).first()


def list_instances_by_status(db: Session, statuses: list[str]) -> list[RuntimeInstance]:
    return list(db.scalars(select(RuntimeInstance).where(RuntimeInstance.status.in_(statuses))))


def list_instances_by_runtime_type(db: Session, runtime_type: str) -> list[RuntimeInstance]:
    return list(
        db.scalars(select(RuntimeInstance).where(RuntimeInstance.runtime_type == runtime_type))
    )


def create_instance(db: Session, **fields) -> RuntimeInstance:
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


def get_brain(db: Session, employee_id: int) -> EmployeeBrain | None:
    return db.scalars(select(EmployeeBrain).where(EmployeeBrain.employee_id == employee_id)).first()


def ensure_brain(db: Session, employee_id: int, **defaults) -> EmployeeBrain:
    brain = get_brain(db, employee_id)
    if brain is None:
        brain = EmployeeBrain(employee_id=employee_id, **defaults)
        db.add(brain)
        db.flush()
    return brain
