import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Users } from "lucide-react";
import type { Department, Employee } from "../../types";
import { EmployeePresence } from "../employee/employee-presence";
import { Panel, SectionHeader } from "../shared/panel";

export function WorkforceOverview({
  employees,
  departments,
}: {
  employees: Employee[];
  departments: Department[];
}) {
  const { t } = useTranslation();
  const departmentById = new Map(departments.map((department) => [department.id, department]));
  const ordered = [...employees]
    .sort((a, b) => Number(b.status !== "offline") - Number(a.status !== "offline"))
    .slice(0, 6);
  return (
    <Panel className="p-5 md:p-6">
      <SectionHeader
        kicker={t("dashboard:workforce.kicker")}
        title={t("dashboard:workforce.title")}
        description={t("dashboard:workforce.description")}
        icon={Users}
        action={
          <Link
            to="/employees"
            className="flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
          >
            {t("dashboard:viewAll")}
            <ArrowRight className="h-3 w-3" />
          </Link>
        }
      />
      <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
        {ordered.map((employee) => (
          <EmployeePresence
            key={employee.id}
            employee={employee}
            department={departmentById.get(employee.department_id)}
          />
        ))}
      </div>
    </Panel>
  );
}
