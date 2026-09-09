import { LoaderCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useAuth } from "./auth-context";

export function AuthGuard() {
  const { t } = useTranslation("auth");
  const { auth, isLoading, error } = useAuth();
  const location = useLocation();
  if (isLoading)
    return (
      <div className="flex min-h-screen items-center justify-center bg-background" aria-busy="true">
        <LoaderCircle className="h-6 w-6 animate-spin text-primary" aria-label={t("loading")} />
      </div>
    );
  if (!auth || (error instanceof ApiError && error.status === 401)) {
    return <Navigate to="/auth/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

export function GuestGuard() {
  const { auth, isLoading } = useAuth();
  if (isLoading) return null;
  if (auth) return <Navigate to="/" replace />;
  return <Outlet />;
}
