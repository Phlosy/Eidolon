import { Navigate, Outlet } from "react-router-dom";
import { useCompany } from "./company-context";

/** Everything under the shell needs a company; without one, found it first. */
export function CompanyGuard() {
  const { current } = useCompany();
  if (!current) return <Navigate to="/onboarding" replace />;
  return <Outlet />;
}
