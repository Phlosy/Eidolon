import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, BookOpen, Check, Search, X } from "lucide-react";
import {
  useKnowledgeItems,
  useProposeKnowledgePromotion,
  useReviewKnowledgeProposal,
} from "../../hooks/useKnowledge";
import { useCompany } from "../../hooks/useSystem";
import { useEmployees } from "../../hooks/useEmployees";
import { Badge } from "../../components/common/badge";
import { Button } from "../../components/common/button";
import { Dialog } from "../../components/common/dialog";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { enumLabel } from "../../utils/labels";
import { formatDateTime, formatPercent } from "../../utils/format";
import { cn } from "../../utils/cn";
import type { KnowledgeItem } from "../../types";
import type { PromotionTargetScope } from "../../api/knowledge";

type ScopeFilter = "all" | "company" | "department";

const STATUS_VARIANT = {
  active: "success",
  proposed: "warning",
  rejected: "danger",
} as const;

function promotionTargets(item: KnowledgeItem): PromotionTargetScope[] {
  return (["department", "company"] as PromotionTargetScope[]).filter(
    (target) => target !== item.scope,
  );
}

function KnowledgeItemCard({
  item,
  ownerName,
  departmentName,
  onPropose,
}: {
  item: KnowledgeItem;
  ownerName: string | null;
  departmentName: string | null;
  onPropose: (item: KnowledgeItem) => void;
}) {
  const { t } = useTranslation();
  const review = useReviewKnowledgeProposal();
  const canPropose = item.status === "active" && item.scope !== "company";
  return (
    <li className="command-panel flex flex-col gap-3 p-4" data-testid={`knowledge-item-${item.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">{item.title}</p>
        <Badge variant="info">{enumLabel(t, "knowledge:scope", item.scope)}</Badge>
        <Badge variant={STATUS_VARIANT[item.status] ?? "default"}>
          {enumLabel(t, "knowledge:status", item.status)}
        </Badge>
        {item.topic ? (
          <span className="ml-auto text-xs text-muted-foreground">{item.topic}</span>
        ) : null}
      </div>
      <p className="line-clamp-3 text-sm text-muted-foreground">{item.content}</p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        {ownerName ? (
          <span>
            {t("knowledge:item.owner")} · {ownerName}
          </span>
        ) : null}
        {departmentName ? (
          <span>
            {t("knowledge:item.department")} · {departmentName}
          </span>
        ) : null}
        {item.confidence != null ? (
          <span>{t("knowledge:item.confidence", { value: formatPercent(item.confidence) })}</span>
        ) : null}
        <span className="ml-auto">
          {t("knowledge:item.updatedAt", { value: formatDateTime(item.updated_at) })}
        </span>
      </div>
      {item.status === "proposed" && item.proposed_scope ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warning/25 bg-warning/5 px-3 py-2">
          <span className="mr-auto text-xs text-muted-foreground">
            {t("knowledge:review.pending", {
              scope: enumLabel(t, "knowledge:scope", item.proposed_scope),
            })}
          </span>
          <Button
            size="sm"
            disabled={review.isPending}
            onClick={() => review.mutate({ itemId: item.id, approve: true })}
          >
            <Check className="h-3.5 w-3.5" />
            {t("knowledge:review.approve")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={review.isPending}
            onClick={() => review.mutate({ itemId: item.id, approve: false })}
          >
            <X className="h-3.5 w-3.5" />
            {t("knowledge:review.reject")}
          </Button>
        </div>
      ) : canPropose ? (
        <div className="flex justify-end">
          <Button size="sm" variant="outline" onClick={() => onPropose(item)}>
            <ArrowUpRight className="h-3.5 w-3.5" />
            {t("knowledge:propose.button")}
          </Button>
        </div>
      ) : null}
    </li>
  );
}

function ProposeDialog({ item, onClose }: { item: KnowledgeItem | null; onClose: () => void }) {
  const { t } = useTranslation();
  const propose = useProposeKnowledgePromotion();
  const targets = item ? promotionTargets(item) : [];
  const [target, setTarget] = useState<PromotionTargetScope>("company");
  const effectiveTarget = targets.includes(target) ? target : (targets[0] ?? "company");
  return (
    <Dialog
      open={item != null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("knowledge:propose.dialogTitle")}
      description={
        item ? t("knowledge:propose.dialogDescription", { title: item.title }) : undefined
      }
    >
      {item ? (
        <div className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">
              {t("knowledge:propose.targetLabel")}
            </span>
            <select
              value={effectiveTarget}
              onChange={(event) => setTarget(event.target.value as PromotionTargetScope)}
              className="h-10 w-full rounded-xl border border-border bg-background/55 px-3 text-sm outline-none focus:border-border-active"
            >
              {targets.map((scope) => (
                <option key={scope} value={scope}>
                  {enumLabel(t, "knowledge:scope", scope)}
                </option>
              ))}
            </select>
          </label>
          {propose.isError ? (
            <p className="text-xs text-danger">
              {propose.error instanceof Error ? propose.error.message : t("common:errorFallback")}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={onClose}>
              {t("common:cancel")}
            </Button>
            <Button
              size="sm"
              disabled={propose.isPending}
              onClick={() =>
                propose.mutate(
                  { itemId: item.id, targetScope: effectiveTarget },
                  { onSuccess: onClose },
                )
              }
            >
              {t("knowledge:propose.submit")}
            </Button>
          </div>
        </div>
      ) : null}
    </Dialog>
  );
}

export function KnowledgePage() {
  const { t } = useTranslation();
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>("all");
  const [topic, setTopic] = useState("");
  const [proposing, setProposing] = useState<KnowledgeItem | null>(null);
  const knowledgeQuery = useKnowledgeItems({
    scope: scopeFilter === "all" ? undefined : scopeFilter,
    topic: topic.trim() || undefined,
  });
  const companyQuery = useCompany();
  const employeesQuery = useEmployees();

  const departmentById = useMemo(
    () =>
      new Map(
        (companyQuery.data?.departments ?? []).map((department) => [
          department.id,
          department.name,
        ]),
      ),
    [companyQuery.data],
  );
  const employeeById = useMemo(
    () => new Map((employeesQuery.data ?? []).map((employee) => [employee.id, employee.name])),
    [employeesQuery.data],
  );

  const items = knowledgeQuery.data ?? [];
  const sections: Array<{ scope: "company" | "department"; items: KnowledgeItem[] }> =
    scopeFilter === "all"
      ? [
          { scope: "company", items: items.filter((item) => item.scope === "company") },
          { scope: "department", items: items.filter((item) => item.scope === "department") },
        ]
      : [{ scope: scopeFilter, items }];

  const renderCard = (item: KnowledgeItem) => (
    <KnowledgeItemCard
      key={item.id}
      item={item}
      ownerName={
        item.owner_employee_id != null ? (employeeById.get(item.owner_employee_id) ?? null) : null
      }
      departmentName={
        item.department_id != null ? (departmentById.get(item.department_id) ?? null) : null
      }
      onPropose={setProposing}
    />
  );

  return (
    <div className="space-y-5 panel-enter">
      <PageHeader
        icon={BookOpen}
        title={t("knowledge:title")}
        description={t("knowledge:description")}
      />
      <section className="command-panel relative overflow-hidden p-4">
        <div className="relative flex flex-wrap items-center gap-3">
          <div className="flex rounded-xl border border-border bg-background/55 p-1">
            {(["all", "company", "department"] as ScopeFilter[]).map((scope) => (
              <button
                key={scope}
                type="button"
                data-testid={`knowledge-scope-${scope}`}
                onClick={() => setScopeFilter(scope)}
                className={cn(
                  "h-9 rounded-lg px-3 text-xs",
                  scopeFilter === scope
                    ? "bg-surface-interactive text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {scope === "all" ? t("knowledge:scopeAll") : enumLabel(t, "knowledge:scope", scope)}
              </button>
            ))}
          </div>
          <label className="relative min-w-[220px] flex-1 md:max-w-sm">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder={t("knowledge:topicFilter")}
              aria-label={t("knowledge:topicFilter")}
              className="h-11 w-full rounded-xl border border-border bg-background/55 pl-9 pr-3 text-sm outline-none focus:border-border-active focus:ring-2 focus:ring-primary/15"
            />
          </label>
        </div>
      </section>
      {knowledgeQuery.isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((item) => (
            <Skeleton key={item} className="h-44 rounded-[var(--radius-panel)]" />
          ))}
        </div>
      ) : knowledgeQuery.isError ? (
        <ErrorState error={knowledgeQuery.error} onRetry={() => knowledgeQuery.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title={t("knowledge:emptyTitle")} hint={t("knowledge:emptyHint")} />
      ) : (
        sections.map((section) => (
          <section key={section.scope} className="space-y-3">
            <div className="flex items-baseline gap-3">
              <h2 className="text-sm font-semibold">{t(`knowledge:sections.${section.scope}`)}</h2>
              <span className="text-xs text-muted-foreground">
                {t("knowledge:summary", { count: section.items.length })}
              </span>
            </div>
            {section.items.length === 0 ? (
              <EmptyState title={t("knowledge:emptyTitle")} hint={t("knowledge:emptyHint")} />
            ) : (
              <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {section.items.map(renderCard)}
              </ul>
            )}
          </section>
        ))
      )}
      <ProposeDialog item={proposing} onClose={() => setProposing(null)} />
    </div>
  );
}
