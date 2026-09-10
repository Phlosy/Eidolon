import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { useCultivationCharacter } from "../../hooks/useCultivation";
import { Badge } from "../../components/common/badge";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { ProgramPanel } from "../../components/cultivation/program-panel";
import { FreeSessionForm } from "../../components/cultivation/free-session-form";
import { CompleteCultivation } from "../../components/cultivation/complete-cultivation";
import { EducationTimeline } from "../../components/cultivation/education-timeline";
import { CharacterProfile } from "../../components/cultivation/character-profile";
import { enumLabel } from "../../utils/labels";

/**
 * 角色详情：培养实例（进度/推进）+ 自由养成会话 + 履历时间线 + 成品档案。
 *
 * 自由养成表单只在「没有进行中的模板 + 仍在培养期」时出现 —— 与后端引擎的
 * 拒绝条件（active program / 已 ready）保持一致，不制造注定 409 的入口。
 */
export function CultivationDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const characterId = Number(id);
  const query = useCultivationCharacter(Number.isFinite(characterId) ? characterId : null);

  if (query.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }
  if (query.isError || !query.data) {
    return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  }

  const character = query.data;
  const activeProgram = character.programs.find((program) => program.status === "active");
  const canRunFreeSession = character.lifecycle === "cultivating" && !activeProgram;

  return (
    <div className="space-y-5 panel-enter">
      <div>
        <Link
          to="/cultivation"
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          {t("cultivation:detail.back")}
        </Link>
      </div>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold">{character.name}</h1>
            <Badge variant="muted">{enumLabel(t, "cultivation:origin", character.origin)}</Badge>
            <Badge
              variant={character.lifecycle === "ready" ? "success" : "info"}
              data-testid="lifecycle-badge"
            >
              {enumLabel(t, "cultivation:lifecycle", character.lifecycle)}
            </Badge>
          </div>
          <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
            <span>
              {t("cultivation:detail.identity")}: {character.identity_id}
            </span>
            <span>
              {t("cultivation:detail.createdAt", {
                value: new Date(character.created_at).toLocaleDateString(),
              })}
            </span>
          </p>
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          {character.programs.length === 0 ? (
            <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
              <h3 className="text-sm font-semibold">{t("cultivation:detail.programsTitle")}</h3>
              <p className="mt-2 text-xs text-muted-foreground">
                {t("cultivation:detail.noProgram")}
              </p>
            </section>
          ) : (
            character.programs.map((program) => <ProgramPanel key={program.id} program={program} />)
          )}

          {canRunFreeSession ? <FreeSessionForm characterId={character.id} /> : null}
          {canRunFreeSession ? <CompleteCultivation characterId={character.id} /> : null}

          <EducationTimeline events={character.events} />
        </div>

        <aside>
          <CharacterProfile character={character} />
        </aside>
      </div>
    </div>
  );
}
