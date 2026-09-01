import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import { formatPercent } from "../../utils/format";
import type { Skill, SkillValidationStatus } from "../../types";

const VALIDATION_VARIANT: Record<SkillValidationStatus, "muted" | "success" | "warning"> = {
  candidate: "warning",
  validated: "success",
  deprecated: "muted",
};

export function SkillsTable({ skills }: { skills: Skill[] }) {
  const { t } = useTranslation();
  if (skills.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("employee:skills.emptyTitle")}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="py-2 pr-4 font-medium">{t("employee:skills.table.skill")}</th>
            <th className="py-2 pr-4 font-medium">{t("employee:skills.table.version")}</th>
            <th className="py-2 pr-4 font-medium">{t("employee:skills.table.attempts")}</th>
            <th className="py-2 pr-4 font-medium">{t("employee:skills.table.successRate")}</th>
            <th className="py-2 font-medium">{t("employee:skills.table.validation")}</th>
          </tr>
        </thead>
        <tbody>
          {skills.map((skill) => (
            <tr key={skill.id} className="border-b border-border/60 last:border-0">
              <td className="py-2 pr-4 font-medium">{skill.name}</td>
              <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">{skill.version}</td>
              <td className="py-2 pr-4">{skill.attempts}</td>
              <td className="py-2 pr-4">{formatPercent(skill.success_rate)}</td>
              <td className="py-2">
                <Badge variant={VALIDATION_VARIANT[skill.validation_status]}>
                  {enumLabel(t, "employee:skills.validation", skill.validation_status)}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
