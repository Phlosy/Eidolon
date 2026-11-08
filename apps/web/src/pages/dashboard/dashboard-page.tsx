import { CircleGauge, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { work as workApi } from "../../api";
import { DecisionCard } from "../../components/app/decision-card";
import { ErrorNote, Section, Stat, StatusBadge } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { Dialog } from "../../components/common/dialog";
import { Input, Textarea } from "../../components/common/input";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import {
  useCommand,
  useCompanyQuery,
  useDecisions,
  useEmployees,
  useLedger,
  useMissions,
  useProjects,
} from "../../hooks/queries";
import { formatMicro, formatRelativeTime } from "../../utils/format";

export function DashboardPage() {
  const { t } = useTranslation("app");
  const { company_id: companyId } = useCurrentCompany();
  const company = useCompanyQuery(companyId);
  const decisions = useDecisions(companyId);
  const missions = useMissions(companyId);
  const projects = useProjects(companyId);
  const employees = useEmployees(companyId);
  const ledger = useLedger(companyId);
  const [proposing, setProposing] = useState(false);
  const currency = company.data?.currency;
  return (
    <>
      <PageHeader
        title={company.data?.name ?? t("dashboard.title")}
        description={t("dashboard.description")}
        icon={CircleGauge}
        actions={
          <Button onClick={() => setProposing(true)}>
            <Plus className="h-4 w-4" />
            {t("dashboard.proposeMission")}
          </Button>
        }
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label={t("dashboard.missions")} value={missions.data?.length ?? "…"} />
        <Stat
          label={t("dashboard.projects")}
          value={projects.data?.filter((p) => p.status === "active").length ?? "…"}
          hint={projects.data ? `${projects.data.length} total` : undefined}
        />
        <Stat label={t("dashboard.employees")} value={employees.data?.length ?? "…"} />
        <Stat
          label={t("dashboard.spend")}
          value={ledger.data ? formatMicro(ledger.data.spend_micro, currency) : "…"}
          hint={
            company.data?.institution
              ? `${company.data.institution.template_id} v${company.data.institution.version}`
              : undefined
          }
        />
      </div>
      <div className="mt-6 grid gap-6 xl:grid-cols-[3fr_2fr]">
        <Section title={t("dashboard.decisions")}>
          {decisions.data?.length ? (
            <div className="command-panel mb-4 p-4" data-testid="today">
              <p className="text-sm font-semibold">
                {t("dashboard.today", { count: decisions.data.length })}
              </p>
              <p className="text-xs text-muted-foreground">{t("dashboard.todayHint")}</p>
              <ul className="mt-2 space-y-1 text-sm">
                {decisions.data.map((d) => (
                  <li key={d.decision_id} className="flex flex-wrap gap-2">
                    <span className="font-mono text-xs text-muted-foreground">{d.gate}</span>
                    <span>
                      {d.brief?.summary ?? t("decision.briefNone")}
                      {d.brief
                        ? ` (${t(`decision.recommend.${d.brief.recommendation}`, d.brief.recommendation)})`
                        : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {decisions.isLoading ? <Skeleton className="h-24 w-full" /> : null}
          {decisions.error ? (
            <ErrorState error={decisions.error} onRetry={() => void decisions.refetch()} />
          ) : null}
          {decisions.data && decisions.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("dashboard.noDecisions")}</p>
          ) : null}
          <div className="space-y-3">
            {decisions.data?.map((d) => (
              <DecisionCard key={d.decision_id} decision={d} />
            ))}
          </div>
        </Section>
        <Section title={t("mission.list")}>
          {missions.isLoading ? <Skeleton className="h-24 w-full" /> : null}
          {missions.data && missions.data.length === 0 ? (
            <EmptyState title={t("projects.empty")} />
          ) : null}
          <ul className="space-y-2">
            {missions.data?.map((m) => (
              <li key={m.mission_id} className="rounded-xl border border-border p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{m.title}</span>
                  <StatusBadge status={m.status} />
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{m.goal}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {formatRelativeTime(m.created_at)}
                  {m.budget != null ? ` · ${m.budget} units` : ""}
                </p>
              </li>
            ))}
          </ul>
          {projects.data?.length ? (
            <p className="mt-3 text-xs">
              <Link to="/projects" className="text-primary hover:underline">
                {t("projects.title")} →
              </Link>
            </p>
          ) : null}
        </Section>
      </div>
      <ProposeMissionDialog
        open={proposing}
        onClose={() => setProposing(false)}
        companyId={companyId}
      />
    </>
  );
}

function ProposeMissionDialog({
  open,
  onClose,
  companyId,
}: {
  open: boolean;
  onClose: () => void;
  companyId: string;
}) {
  const { t } = useTranslation("app");
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [criteria, setCriteria] = useState("");
  const [constraints, setConstraints] = useState("");
  const [budget, setBudget] = useState("");
  const [deadline, setDeadline] = useState("");
  const [key, setKey] = useState(() => crypto.randomUUID());
  const propose = useCommand(() =>
    workApi.proposeMission(
      companyId,
      {
        title,
        goal,
        success_criteria: lines(criteria),
        constraints: lines(constraints),
        budget: budget ? Number(budget) : null,
        deadline: deadline || null,
      },
      key,
    ),
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    propose.mutate(undefined, {
      onSuccess: () => {
        setTitle("");
        setGoal("");
        setCriteria("");
        setConstraints("");
        setBudget("");
        setKey(crypto.randomUUID());
        onClose();
      },
    });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => (o ? undefined : onClose())}
      title={t("mission.title")}
      description={t("mission.hint")}
    >
      <form onSubmit={submit} className="space-y-3">
        <Input
          aria-label={t("mission.title")}
          placeholder={t("mission.title")}
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <Textarea
          aria-label={t("mission.goal")}
          placeholder={t("mission.goal")}
          required
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />
        <Textarea
          aria-label={t("mission.criteria")}
          placeholder={t("mission.criteria")}
          value={criteria}
          onChange={(e) => setCriteria(e.target.value)}
        />
        <Textarea
          aria-label={t("mission.constraints")}
          placeholder={t("mission.constraints")}
          className="min-h-16"
          value={constraints}
          onChange={(e) => setConstraints(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-3">
          <Input
            aria-label={t("mission.budget")}
            placeholder={t("mission.budget")}
            type="number"
            min={0}
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
          <Input
            aria-label={t("mission.deadline")}
            placeholder={t("mission.deadline")}
            value={deadline}
            onChange={(e) => setDeadline(e.target.value)}
          />
        </div>
        <ErrorNote error={propose.error} />
        <Button type="submit" className="w-full" disabled={propose.isPending}>
          {t("mission.submit")}
        </Button>
      </form>
    </Dialog>
  );
}

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((v) => v.trim())
    .filter(Boolean);
}
