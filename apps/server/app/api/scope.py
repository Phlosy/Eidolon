"""请求级租户边界。放在 API 层共用，避免每个 router 自己解一次公司。"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import get_request_identity
from app.repositories import organization as org_repo


def resolve_company_id(db: Session = Depends(get_db)) -> int | None:
    """当前请求的公司边界。

    沿用现有约定，**不新增鉴权模型**（`position-system.md §8` 明确要求）：本项目是单公司
    部署，`company.py` 也是直接取默认公司；已登录时以会话身份为准。写端点的授权面跟
    其它写端点一致 —— 挂在 `protected` 路由下（`require_user`），这里不再自造 admin 角色。
    """
    identity = get_request_identity()
    if identity is not None:
        return identity.company_id
    company = org_repo.get_default_company(db)
    return company.id if company is not None else None
