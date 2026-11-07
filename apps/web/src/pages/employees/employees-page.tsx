import { Plus, Users } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { companies as companiesApi } from "../../api";
import type { Employee } from "../../api/types";
import { ErrorNote } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { Dialog } from "../../components/common/dialog";
import { Input, Textarea } from "../../components/common/input";
import { ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import { useCommand, useEmployees, useInstitution } from "../../hooks/queries";
import { formatDateTime, initials } from "../../utils/format";

export function EmployeesPage() {
  const { t } = useTranslation("app");
  const me = useCurrentCompany();
  const employees = useEmployees(me.company_id);
  const [hiring, setHiring] = useState(false);
  return (
    <>
      <PageHeader
        title={t("employees.title")}
        description={t("employees.description")}
        icon={Users}
        actions={
          me.role === "owner" ? (
            <Button onClick={() => setHiring(true)}>
              <Plus className="h-4 w-4" />
              {t("employees.hire")}
            </Button>
          ) : undefined
        }
      />
      {employees.isLoading ? <Skeleton className="h-40 w-full" /> : null}
      {employees.error ? (
        <ErrorState error={employees.error} onRetry={() => void employees.refetch()} />
      ) : null}
      <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {employees.data?.map((e) => (
          <EmployeeCard
            key={e.employment_id}
            employee={e}
            canEnd={me.role === "owner" && e.role !== "owner"}
          />
        ))}
      </ul>
      <HireDialog open={hiring} onClose={() => setHiring(false)} />
    </>
  );
}

function EmployeeCard({ employee, canEnd }: { employee: Employee; canEnd: boolean }) {
  const { t } = useTranslation("app");
  const me = useCurrentCompany();
  const [ending, setEnding] = useState(false);
  const [reason, setReason] = useState("");
  const end = useCommand(() =>
    companiesApi.endEmployment(me.company_id, employee.employment_id, reason),
  );
  return (
    <li className="command-panel p-4" data-testid="employee-card">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-xs font-semibold text-primary">
          {initials(employee.name)}
        </span>
        <div className="min-w-0 flex-1">
          <Link
            to={`/employees/${encodeURIComponent(employee.person_id)}`}
            className="text-sm font-semibold hover:underline"
          >
            {employee.name}
          </Link>
          <p className="text-xs text-muted-foreground">
            {employee.person_id} · {employee.role}
            {employee.title ? ` · ${employee.title}` : ""} ·{" "}
            {t(`employees.${employee.kind ?? "agent"}`)}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {t("employees.since")} {formatDateTime(employee.started_at)}
          </p>
        </div>
      </div>
      {employee.competencies.length ? (
        <ul className="mt-3 flex flex-wrap gap-1.5">
          {employee.competencies.map((c) => (
            <li key={c.area} className="rounded-full border border-border px-2 py-0.5 text-[11px]">
              {c.area} <span className="font-mono">{c.score}</span>
              <span className="text-muted-foreground"> · {c.evidence_count}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {canEnd ? (
        <div className="mt-3 text-right">
          {ending ? (
            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                end.mutate(undefined, { onSuccess: () => setEnding(false) });
              }}
            >
              <Input
                aria-label={t("employees.endReason")}
                placeholder={t("employees.endReason")}
                required
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <Button size="sm" variant="destructive" type="submit" disabled={end.isPending}>
                {t("employees.end")}
              </Button>
            </form>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setEnding(true)}>
              {t("employees.end")}
            </Button>
          )}
          <ErrorNote error={end.error} />
        </div>
      ) : null}
    </li>
  );
}

function HireDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation("app");
  const me = useCurrentCompany();
  const institution = useInstitution(me.company_id);
  const agentRoles = institution.data?.template.roles.filter((r) => r.kind === "agent") ?? [];
  const [personId, setPersonId] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [title, setTitle] = useState("");
  const [persona, setPersona] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const hire = useCommand((body: Parameters<typeof companiesApi.hire>[1]) =>
    companiesApi.hire(me.company_id, body),
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    let parsed: Record<string, unknown> | undefined;
    if (persona.trim()) {
      try {
        parsed = JSON.parse(persona) as Record<string, unknown>;
      } catch (e) {
        setParseError(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    setParseError(null);
    hire.mutate(
      {
        person_id: personId,
        role: role || agentRoles[0]?.code || "",
        name: name || undefined,
        title: title || undefined,
        persona: parsed,
      },
      {
        onSuccess: () => {
          setPersonId("");
          setName("");
          setTitle("");
          setPersona("");
          onClose();
        },
      },
    );
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => (o ? undefined : onClose())}
      title={t("employees.hire")}
    >
      <form onSubmit={submit} className="space-y-3">
        <Input
          aria-label={t("employees.personId")}
          placeholder={t("employees.personId")}
          required
          pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
          value={personId}
          onChange={(e) => setPersonId(e.target.value)}
        />
        <Input
          aria-label={t("employees.name")}
          placeholder={t("employees.name")}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select
          aria-label={t("employees.role")}
          className="h-10 w-full rounded-md border border-border bg-transparent px-3 text-sm [&>option]:bg-card"
          value={role || agentRoles[0]?.code || ""}
          onChange={(e) => setRole(e.target.value)}
        >
          {agentRoles.map((r) => (
            <option key={r.code} value={r.code}>
              {r.title} ({r.code})
            </option>
          ))}
        </select>
        <Input
          aria-label={t("employees.titleField")}
          placeholder={t("employees.titleField")}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <Textarea
          aria-label={t("employees.persona")}
          placeholder={t("employees.persona")}
          className="min-h-16 font-mono text-xs"
          value={persona}
          onChange={(e) => setPersona(e.target.value)}
        />
        <ErrorNote error={parseError ?? hire.error} />
        <Button
          type="submit"
          className="w-full"
          disabled={hire.isPending || agentRoles.length === 0}
        >
          {t("employees.submit")}
        </Button>
      </form>
    </Dialog>
  );
}
