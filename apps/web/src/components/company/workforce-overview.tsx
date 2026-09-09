import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Users } from "lucide-react";
import type { Department, Employee } from "../../types";
import { EmployeePresence } from "../employee/employee-presence";
import { Panel, SectionHeader } from "../shared/panel";

/**
 * 员工名册 → 横排「队伍」条。
 *
 * 游戏里的队伍是横向一排头像；这里沿用同样的信息形态：一行扫完谁在岗、
 * 谁在忙，点进去看详情。不再用纵向网格占掉半屏。
 */
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
    .slice(0, 8);
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
      <div className="scroll-area -mx-1 flex gap-2.5 overflow-x-auto px-1 pb-1">
        {ordered.map((employee) => (
          <div key={employee.id} className="w-[218px] shrink-0">
            <EmployeePresence
              employee={employee}
              department={departmentById.get(employee.department_id)}
            />
          </div>
        ))}
      </div>
    </Panel>
  );
}
