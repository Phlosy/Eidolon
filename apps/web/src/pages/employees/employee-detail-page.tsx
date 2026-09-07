import { useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  useEmployee,
  useEmployeeKnowledge,
  useEmployeeLearningRecords,
  useEmployeePerformance,
  useEmployeeSkills,
  useTask,
} from "../../hooks/useEmployees";
import { useCompany } from "../../hooks/useSystem";
import { useEmployeeRuntime } from "../../hooks/useRuntimes";
import { useProvisioningJob, useProvisioningJobs } from "../../hooks/useLifecycle";
import { ProfileSection } from "../../components/employee/profile-section";
import { EmployeeSkillPanel } from "../../components/employee/employee-skill-panel";
import { EmployeeHero } from "../../components/employee/employee-hero";
import { KnowledgeList, LearningRecordsList } from "../../components/employee/learning-knowledge";
import { EmployeeActivity } from "../../components/employee/employee-activity";
import { RuntimeTab } from "../../components/employee/runtime-tab";
import { WorkspaceTab } from "../../components/employee/workspace-tab";
import { MemoryTab } from "../../components/employee/memory-tab";
import { PerformanceTab } from "../../components/employee/performance-tab";
import { BehaviorTab } from "../../components/employee/behavior-tab";
import { CapabilitiesTab } from "../../components/employee/capabilities-tab";
import { CareerTab } from "../../components/employee/career-tab";
import { LifecycleActions } from "../../components/lifecycle/lifecycle-actions";
import { ProvisioningJobPanel } from "../../components/lifecycle/provisioning-job-panel";
import { LifecycleTimeline } from "../../components/lifecycle/lifecycle-timeline";
import { EmploymentTab } from "../../components/lifecycle/employment-tab";
import { AccountsTab } from "../../components/lifecycle/accounts-tab";
import { AccessTab } from "../../components/lifecycle/access-tab";
import { AssetsTab } from "../../components/lifecycle/assets-tab";
import { Panel } from "../../components/shared/panel";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { cn } from "../../utils/cn";

type Tab =
  | "overview"
  | "employment"
  | "runtime"
  | "activity"
  | "accounts"
  | "access"
  | "assets"
  | "workspace"
  | "memory"
  | "knowledge"
  | "skills"
  | "learning"
  | "behavior"
  | "capabilities"
  | "performance"
  | "career";

/**
 * 教程 target：Tab 与 api 无关，是纯 UI 锚点。
 * 没列出来的 Tab 保持 undefined（React 会省略该属性），避免误打光。
 */
const TUTORIAL_TAB_TARGETS: Partial<Record<Tab, string>> = {
  runtime: "employee-runtime-tab",
  accounts: "employee-accounts-tab",
};

const TABS: Tab[] = [
  "overview",
  "employment",
  "runtime",
  "activity",
  "accounts",
  "access",
  "assets",
  "workspace",
  "memory",
  "knowledge",
  "skills",
  "learning",
  "behavior",
  "capabilities",
  "performance",
  "career",
];

export function EmployeeDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const employeeId = Number(id);
  const [tab, setTab] = useState<Tab>("overview");
  // Job started via the hire wizard (navigate state) or a lifecycle action.
  const location = useLocation();
  const [activeJobId, setActiveJobId] = useState<number | null>(
    (location.state as { jobId?: number } | null)?.jobId ?? null,
  );

  const employeeQuery = useEmployee(employeeId);
  const skillsQuery = useEmployeeSkills(employeeId);
  const learningQuery = useEmployeeLearningRecords(employeeId);
  const knowledgeQuery = useEmployeeKnowledge(employeeId);
  const companyQuery = useCompany();
  const runtimeQuery = useEmployeeRuntime(employeeId);
  const performanceQuery = useEmployeePerformance(employeeId);
  const jobsQuery = useProvisioningJobs(employeeId);
  const trackedJobQuery = useProvisioningJob(activeJobId);
  const taskQuery = useTask(employeeQuery.data?.current_task_id);

  if (employeeQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (employeeQuery.isError || !employeeQuery.data) {
    return <ErrorState error={employeeQuery.error} onRetry={() => employeeQuery.refetch()} />;
  }
  const employee = employeeQuery.data;

  // Show the tracked job, else the employee's latest job when it isn't done.
  const latestJob =
    (jobsQuery.data ?? []).length > 0
      ? jobsQuery.data!.reduce((a, b) => (a.id > b.id ? a : b))
      : null;
  const trackedJob = trackedJobQuery.data ?? null;
  const panelJobId =
    trackedJob && trackedJob.status !== "done"
      ? trackedJob.id
      : activeJobId != null && trackedJobQuery.isLoading
        ? activeJobId
        : latestJob && latestJob.status !== "done"
          ? latestJob.id
          : null;

  return (
    <div className="space-y-5 panel-enter">
      <EmployeeHero
        employee={employee}
        department={companyQuery.data?.departments.find(
          (department) => department.id === employee.department_id,
        )}
        runtime={runtimeQuery.data ?? undefined}
        task={taskQuery.data}
        performance={performanceQuery.data}
        actions={<LifecycleActions employee={employee} onJobStarted={setActiveJobId} />}
      />

      {panelJobId != null ? (
        <div className="mb-5">
          <ProvisioningJobPanel jobId={panelJobId} />
        </div>
      ) : null}

      <div className="scroll-area overflow-x-auto rounded-2xl border border-border bg-surface/85 p-1.5 shadow-[var(--shadow-panel)]">
        <nav className="flex min-w-max gap-1">
          {TABS.map((key) => (
            <button
              key={key}
              data-tutorial-target={TUTORIAL_TAB_TARGETS[key]}
              onClick={() => setTab(key)}
              className={cn(
                "rounded-xl px-3 py-2 text-xs whitespace-nowrap text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                tab === key && "bg-primary/10 font-medium text-primary",
              )}
            >
              {t(`employee:tabs.${key}`)}
            </button>
          ))}
        </nav>
      </div>

      <Panel className="p-5 md:p-6">
        {tab === "overview" ? (
          <div className="space-y-6">
            <ProfileSection employee={employee} />
            <LifecycleTimeline employeeId={employeeId} />
          </div>
        ) : null}
        {tab === "employment" ? <EmploymentTab employeeId={employeeId} /> : null}
        {tab === "runtime" ? <RuntimeTab employeeId={employeeId} /> : null}
        {tab === "activity" ? <EmployeeActivity employeeId={employeeId} /> : null}
        {tab === "accounts" ? <AccountsTab employeeId={employeeId} /> : null}
        {tab === "access" ? <AccessTab employeeId={employeeId} /> : null}
        {tab === "assets" ? <AssetsTab employeeId={employeeId} /> : null}
        {tab === "workspace" ? <WorkspaceTab employee={employee} /> : null}
        {tab === "memory" ? <MemoryTab employeeId={employeeId} /> : null}
        {tab === "knowledge" ? (
          knowledgeQuery.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : (
            <KnowledgeList items={knowledgeQuery.data ?? []} />
          )
        ) : null}
        {tab === "skills" ? (
          skillsQuery.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : (
            <EmployeeSkillPanel skills={skillsQuery.data ?? []} />
          )
        ) : null}
        {tab === "learning" ? (
          learningQuery.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : (
            <LearningRecordsList records={learningQuery.data ?? []} />
          )
        ) : null}
        {tab === "behavior" ? <BehaviorTab employeeId={employeeId} /> : null}
        {tab === "capabilities" ? <CapabilitiesTab employeeId={employeeId} /> : null}
        {tab === "performance" ? <PerformanceTab employeeId={employeeId} /> : null}
        {tab === "career" ? <CareerTab employeeId={employeeId} /> : null}
      </Panel>
    </div>
  );
}
