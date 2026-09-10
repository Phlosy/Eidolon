import { useTranslation } from "react-i18next";
import { GraduationCap, Loader2 } from "lucide-react";
import { Button } from "../common/button";
import { useCompleteCultivation } from "../../hooks/useCultivation";

/**
 * 养成结业（T2.2，设计 §7 D1）：自由养成的**唯一**完成入口 —— 玩家显式结业。
 *
 * 刻意不做任何"够不够格"的提示或校验：结业不看能力分/证据量（市场价值由买方读履历判断）。
 * 模板线不需要这个入口（走完全部阶段自动 ready），所以调用方只在"无进行中模板"时渲染。
 */
export function CompleteCultivation({ characterId }: { characterId: number }) {
  const { t } = useTranslation();
  const complete = useCompleteCultivation();

  return (
    <section
      className="rounded-2xl border border-border bg-card p-4 shadow-card"
      data-testid="complete-cultivation"
    >
      <h3 className="text-sm font-semibold">{t("cultivation:complete.title")}</h3>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        {t("cultivation:complete.hint")}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          variant="outline"
          data-testid="complete-cultivation-button"
          disabled={complete.isPending}
          onClick={() => complete.mutate(characterId)}
        >
          {complete.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <GraduationCap className="h-3.5 w-3.5" />
          )}
          {complete.isPending
            ? t("cultivation:complete.pending")
            : t("cultivation:complete.button")}
        </Button>
        <p className="text-[11px] leading-4 text-muted-foreground">
          {t("cultivation:complete.irreversible")}
        </p>
      </div>
      {complete.isError ? (
        <p className="mt-2 text-xs text-danger" role="alert">
          {complete.error.message}
        </p>
      ) : null}
    </section>
  );
}
