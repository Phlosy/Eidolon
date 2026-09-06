"""/behavior — 行为策略的服务端预览（招聘向导与员工详情用）。

为什么需要这个接口：档位边界与阈值**只允许住在 `app/brain/`**（契约 §3.3）。前端一旦自己
拿人格数字去比边界，就会出现第二套阈值，改一处漏一处 —— 所以 UI 只把 0..1 的数字发过来，
拿回"这个人格会变成什么工作方式"。

本模块刻意不 import `resolve()`，也不按 trait 名字取值：换算发生在
`app.brain.preview_policy()` 里，业务层只搬运数字（`tests/test_architecture_guards.py`
会拦住越界写法）。参数设计成 `?trait=<名>&value=<数>` 而不是 `?curiosity=<数>`：
将来注册第二个特质（risk_tolerance 等）时，这个端点一行都不用改。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.brain import TraitOutOfRange, UnknownTrait, preview_policy
from app.core.database import get_db
from app.core.request_context import get_request_identity
from app.models.organization import Company

router = APIRouter(prefix="/behavior", tags=["behavior"])


@router.get("/preview")
def preview_behavior(
    trait: str = Query(default="curiosity", description="要预览的人格特质名（须已注册）"),
    value: float = Query(default=0.5, ge=0.0, le=1.0, description="该特质的取值 0..1"),
    learning_enabled: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict:
    """返回给定人格值下生效的 BehaviorPolicy 摘要（含公司级覆盖）。

    越界值由 brain 层夹紧到 0..1；未注册的特质名返回 422，避免前端拼错字段后
    静默显示默认档位。
    """
    identity = get_request_identity()
    company = db.get(Company, identity.company_id) if identity else None
    try:
        policy = preview_policy({trait: value}, learning_enabled=learning_enabled, company=company)
    except (UnknownTrait, TraitOutOfRange, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return policy.as_dict()
