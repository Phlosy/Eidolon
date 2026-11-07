# docs/archive — 旧应用（v0.x / M1–M3 / T2）的设计与证据

这里是 2026-11-07 之前旧应用（`apps/server/app/`，SQLite，116 张表）的全部设计文档、
实施计划、冻结记录与 50 份机器证据 JSON，**原样搬入，不再维护**。旧代码本身在
commit `232ceff` 及之前的历史里。

v2 只认三份文档：`docs/v2-handoff.md`（唯一入口）、`docs/kernel-plan.md`、
`docs/v2-architecture.md`。旧文档里的不变量编号（E/R/H/RV/U 系列）凡仍然有效的，
已经折进 `apps/server/eidolon/kernel/contracts.py` 的 K/W/M/T 系列并由测试锚定；
没折进去的就是被 V1–V6 与 ADR 明确放弃的。
