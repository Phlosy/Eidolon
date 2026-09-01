import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  useEmployee,
  useEmployeeKnowledge,
  useEmployeeLearningRecords,
  useEmployeeSkills,
} from "../../hooks/useEmployees";
import { ProfileSection } from "../../components/employee/profile-section";
import { SkillsTable } from "../../components/employee/skills-table";
import { KnowledgeList, LearningRecordsList } from "../../components/employee/learning-knowledge";
import { EmployeeActivity } from "../../components/employee/employee-activity";
import { RuntimeTab } from "../../components/employee/runtime-tab";
import { WorkspaceTab } from "../../components/employee/workspace-tab";
import { MemoryTab } from "../../components/employee/memory-tab";
import { PerformanceTab } from "../../components/employee/performance-tab";
import { CareerTab } from "../../components/employee/career-tab";
import { Card, CardContent } from "../../components/common/card";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { cn } from "../../utils/cn";

type Tab = "overview" | "runtime" | "activity" | "workspace" | "memory" | "knowledge" |
  "skills" | "learning" | "performance" | "career";

const TABS: Tab[] = [
  "overview",
  "runtime",
  "activity",
  "workspace",
  "memory",
  "knowledge",
  "skills",
  "learning",
  "performance",
  "career",
];

export function EmployeeDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const employeeId = Number(id);
  const [tab, setTab] = useState<Tab>("overview");

  const employeeQuery = useEmployee(employeeId);
  const skillsQuery = useEmployeeSkills(employeeId);
  const learningQuery = useEmployeeLearningRecords(employeeId);
  const knowledgeQuery = useEmployeeKnowledge(employeeId);

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

  return (
    <div>
      <div className="mb-5 overflow-x-auto border-b border-border">
        <nav className="flex gap-1">
          {TABS.map((key) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={cn(
                "border-b-2 border-transparent px-3 py-2 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground",
                tab === key && "border-foreground font-medium text-foreground",
              )}
            >
              {t(`employee:tabs.${key}`)}
            </button>
          ))}
        </nav>
      </div>

      <Card>
        <CardContent className="p-5">
          {tab === "overview" ? <ProfileSection employee={employee} /> : null}
          {tab === "runtime" ? <RuntimeTab employeeId={employeeId} /> : null}
          {tab === "activity" ? <EmployeeActivity employeeId={employeeId} /> : null}
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
              <SkillsTable skills={skillsQuery.data ?? []} />
            )
          ) : null}
          {tab === "learning" ? (
            learningQuery.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <LearningRecordsList records={learningQuery.data ?? []} />
            )
          ) : null}
          {tab === "performance" ? <PerformanceTab employeeId={employeeId} /> : null}
          {tab === "career" ? <CareerTab employeeId={employeeId} /> : null}
        </CardContent>
      </Card>
    </div>
  );
}
