import { useTranslation } from "react-i18next";
import type { TraitView } from "../../types";

/**
 * 人格 8 维列表（T2.1 共享组件）：培养档案 / 市场档案 / 员工档案共用一处渲染。
 *
 * 铁律（概念架构 §4 规则 4/10）：trait 只描述"倾向怎样工作"，不参与任何能力换算；
 * 这里只画倾向条，不显示加成/成功率。
 */
export function TraitsList({
  traits,
  testId = "trait-list",
}: {
  traits: TraitView[];
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <ul className="space-y-1.5" data-testid={testId}>
      {traits.map((trait) => (
        <li key={trait.code} className="flex items-center gap-2">
          <span className="w-20 shrink-0 text-xs">
            {t(`person:traits.${trait.code}`, { defaultValue: trait.label })}
          </span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <span
              className="block h-full rounded-full bg-primary"
              style={{ width: `${Math.round(trait.value * 100)}%` }}
            />
          </span>
          <span className="w-9 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
            {trait.display}
          </span>
        </li>
      ))}
    </ul>
  );
}
