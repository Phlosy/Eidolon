import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import type { ProviderScope } from "../../types";

/** Company providers are shared; employee providers are private to one employee. */
export function ProviderScopeBadge({ scope }: { scope: ProviderScope }) {
  const { t } = useTranslation();
  return scope === "company" ? (
    <Badge variant="info">{t("provider:scope.company")}</Badge>
  ) : (
    <Badge variant="violet">{t("provider:scope.employee")}</Badge>
  );
}
