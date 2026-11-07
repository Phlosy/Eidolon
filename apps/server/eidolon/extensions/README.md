# extensions/

Reserved for optional, non-core subsystems (V4: market, cultivation, tutorial,
third-party runtime adapters). Nothing here is part of the launch scope, and nothing
below this layer may import it (v2-architecture §2.1).

The legacy implementations were **archived, not ported**: the last commit that still
contains `apps/server/app/` (talent market, cultivation, work orders, NPC economy,
tutorial, Hermes/OpenClaw runtime adapters) and their 3 228 tests is

    232ceff  docs(plan): R5-d 填入提交号 36cc52d

Bring one back by re-reading that tree and rewriting it against the fact log and the
projections (kernel-plan §8 R7), never by reviving the old tables.
