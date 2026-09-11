"""内部能力令牌：区分"谁能发行货币"（E4/E23，设计 §8/§17）。

`ledger.transaction_posted` 的 `mint` / `burn` / `treasury_transfer` 三种腿组合**必须**
携带本模块的令牌；令牌只被 `app/services/economy/monetary.py`（MonetaryAuthority）获取。

Python 没有真正的私有，所以这里不假装是安全边界 —— 它是**代码边界**：
"谁在铸币"在调用点上可见、可 grep、可被守卫测试钉住（`tests/test_economy_ledger.py`
断言除 monetary.py 外没有模块 import 本模块）。
"""

from __future__ import annotations

AUTHORITY_TOKEN: object = object()
