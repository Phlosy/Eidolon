import { useCompany } from "../../hooks/useSystem";
import { useEmployees } from "../../hooks/useEmployees";
import { useProjects } from "../../hooks/useProjects";
import { useRuntimeInstances } from "../../hooks/useRuntimes";
import { useDriveTree } from "../../hooks/useDrive";
import { useDashboardStats } from "../../hooks/useDashboardStats";
import { CompanyHero } from "../../components/company/company-hero";
import { WorkforceOverview } from "../../components/company/workforce-overview";
import { ActiveProjectBoard } from "../../components/company/active-project-board";
import { SystemStatusDrawer } from "../../components/company/system-status-drawer";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { CompanyFoundingState } from "../../components/company/company-founding-state";

/**
 * 首页 = 公司全景舞台。
 *
 * 结构：舞台横幅（公司/等级/下一步）→ 队伍 + 任务 → 折叠的系统状态。
 * 一屏一个重心，明细按需展开。
 */
export function DashboardPage() {
  const companyQuery = useCompany();
  const employeesQuery = useEmployees();
  const projectsQuery = useProjects();
  const runtimesQuery = useRuntimeInstances();
  const driveQuery = useDriveTree();
  const stats = useDashboardStats();
  if (companyQuery.isError || employeesQuery.isError || projectsQuery.isError || stats.isError)
    return <ErrorState error={companyQuery.error ?? employeesQuery.error ?? projectsQuery.error} />;
  if (
    companyQuery.isLoading ||
    employeesQuery.isLoading ||
    projectsQuery.isLoading ||
    stats.isLoading
  )
    return (
      <div className="space-y-5" aria-busy="true">
        <Skeleton className="h-[340px] w-full rounded-[var(--radius-panel)]" />
        <div className="grid gap-5 xl:grid-cols-2">
          <Skeleton className="h-[420px] rounded-[var(--radius-panel)]" />
          <Skeleton className="h-[420px] rounded-[var(--radius-panel)]" />
        </div>
      </div>
    );
  const employees = employeesQuery.data ?? [];
  if (employees.length === 0) return <CompanyFoundingState company={companyQuery.data} />;
  const runtimes = runtimesQuery.data ?? [];
  const incidents = runtimes.filter((runtime) =>
    ["unhealthy", "crashed", "error"].includes(runtime.status),
  ).length;
  const runtimeHealth =
    runtimes.length === 0
      ? 100
      : Math.round(((runtimes.length - incidents) / runtimes.length) * 100);
  return (
    <div className="space-y-5">
      <CompanyHero company={companyQuery.data} stats={stats} runtimeHealth={runtimeHealth} />
      <div className="grid gap-5 xl:grid-cols-[1.15fr_.85fr]">
        <WorkforceOverview
          employees={employees}
          departments={companyQuery.data?.departments ?? []}
        />
        <ActiveProjectBoard
          projects={projectsQuery.data ?? []}
          details={stats.projectDetails}
          employees={employees}
        />
      </div>
      <SystemStatusDrawer employees={employees} runtimes={runtimes} nodes={driveQuery.data ?? []} />
    </div>
  );
}
