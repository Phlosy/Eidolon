import { AccessPackagesSection } from "../../components/lifecycle/access-packages-section";
import { ProvidersOverviewTable } from "../../components/provider/providers-overview-table";

/** 服务商与访问包：模型服务来源与员工权限包。 */
export function ProvidersSection() {
  return (
    <div className="space-y-5">
      <ProvidersOverviewTable />
      <AccessPackagesSection />
    </div>
  );
}
