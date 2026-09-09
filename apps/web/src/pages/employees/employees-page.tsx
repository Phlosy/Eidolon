import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { LayoutGrid, Rows3, Search, UserPlus, Users } from "lucide-react";
import { useEmployees } from "../../hooks/useEmployees";
import { useCompany } from "../../hooks/useSystem";
import { useRuntimeInstances } from "../../hooks/useRuntimes";
import { useTutorial } from "../../hooks/useTutorial";
import { Button } from "../../components/common/button";
import { HireWizard } from "../../components/lifecycle/hire-wizard";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { EmployeeRosterCard } from "../../components/employee/employee-roster-card";
import { cn } from "../../utils/cn";
import type { EmployeeStatus } from "../../types";

export function EmployeesPage() {
  const { t } = useTranslation();
  const employeesQuery = useEmployees();
  const companyQuery = useCompany();
  const runtimes = useRuntimeInstances().data ?? [];
  const tutorial = useTutorial().data;
  const [hireOpen, setHireOpen] = useState(false);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<EmployeeStatus | "all">("all");
  const employees = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (employeesQuery.data ?? []).filter(
      (employee) =>
        (status === "all" || employee.status === status) &&
        (!query ||
          `${employee.name} ${employee.title ?? ""} ${employee.role}`
            .toLowerCase()
            .includes(query)),
    );
  }, [employeesQuery.data, search, status]);
  const departmentById = new Map(
    (companyQuery.data?.departments ?? []).map((department) => [department.id, department]),
  );
  const runtimeByEmployee = new Map(runtimes.map((runtime) => [runtime.employee_id, runtime]));
  const active = (employeesQuery.data ?? []).filter(
    (employee) => employee.status !== "offline",
  ).length;
  const presetRole =
    tutorial?.current_step === "hire_ceo"
      ? "ceo"
      : tutorial?.current_step === "hire_qa"
        ? "qa_engineer"
        : tutorial?.current_step === "hire_engineer"
          ? "engineer"
          : undefined;
  const ceo = employeesQuery.data?.find((employee) => employee.role === "ceo");
  return (
    <div className="space-y-5 panel-enter">
      <PageHeader
        icon={Users}
        title={t("employee:listTitle")}
        description={t("employee:listDescription")}
        actions={
          <Button
            data-testid="hire-button"
            data-tutorial-target="hire-employee"
            onClick={() => setHireOpen(true)}
          >
            <UserPlus className="h-4 w-4" />
            {t("lifecycle:actions.hire")}
          </Button>
        }
      />
      <section className="command-panel relative overflow-hidden p-4">
        <div className="relative flex flex-wrap items-center gap-3">
          <div className="mr-auto">
            <p className="type-kicker text-primary">{t("employee:roster.commandRoster")}</p>
            <p className="type-caption mt-1 text-muted-foreground">
              {t("employee:roster.summary", { active, total: employeesQuery.data?.length ?? 0 })}
            </p>
          </div>
          <label className="relative min-w-[220px] flex-1 md:max-w-sm">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("employee:roster.search")}
              aria-label={t("employee:roster.search")}
              className="h-11 w-full rounded-xl border border-border bg-background/55 pl-9 pr-3 type-body-sm outline-none focus:border-border-active focus:ring-2 focus:ring-primary/15"
            />
          </label>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as EmployeeStatus | "all")}
            aria-label={t("employee:roster.filterStatus")}
            className="h-11 rounded-xl border border-border bg-background/55 px-3 type-caption outline-none focus:border-border-active"
          >
            <option value="all">{t("employee:roster.allStatuses")}</option>
            {(
              [
                "working",
                "researching",
                "learning",
                "meeting",
                "reflecting",
                "idle",
                "offline",
                "error",
              ] as EmployeeStatus[]
            ).map((item) => (
              <option key={item} value={item}>
                {t(`employee:status.${item}`)}
              </option>
            ))}
          </select>
          <div className="flex rounded-xl border border-border bg-background/55 p-1">
            <button
              type="button"
              onClick={() => setView("grid")}
              className={cn(
                "flex h-9 w-9 items-center justify-center rounded-lg",
                view === "grid" ? "bg-surface-interactive text-primary" : "text-muted-foreground",
              )}
              aria-label={t("employee:roster.gridView")}
            >
              <LayoutGrid className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => setView("list")}
              className={cn(
                "flex h-9 w-9 items-center justify-center rounded-lg",
                view === "list" ? "bg-surface-interactive text-primary" : "text-muted-foreground",
              )}
              aria-label={t("employee:roster.listView")}
            >
              <Rows3 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </section>
      {employeesQuery.isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((item) => (
            <Skeleton key={item} className="h-72 rounded-[var(--radius-panel)]" />
          ))}
        </div>
      ) : employeesQuery.isError ? (
        <ErrorState error={employeesQuery.error} onRetry={() => employeesQuery.refetch()} />
      ) : employees.length === 0 ? (
        <EmptyState title={t("employee:emptyTitle")} hint={t("employee:roster.noMatch")} />
      ) : view === "grid" ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {employees.map((employee) => (
            <EmployeeRosterCard
              key={employee.id}
              employee={employee}
              department={departmentById.get(employee.department_id)}
              runtime={runtimeByEmployee.get(employee.id)}
              view="grid"
            />
          ))}
        </div>
      ) : (
        <div className="command-panel overflow-hidden">
          {employees.map((employee) => (
            <EmployeeRosterCard
              key={employee.id}
              employee={employee}
              department={departmentById.get(employee.department_id)}
              runtime={runtimeByEmployee.get(employee.id)}
              view="list"
            />
          ))}
        </div>
      )}
      <HireWizard
        open={hireOpen}
        onOpenChange={setHireOpen}
        presetRole={presetRole}
        presetManagerEmployeeId={presetRole === "ceo" ? null : (ceo?.id ?? null)}
      />
    </div>
  );
}
