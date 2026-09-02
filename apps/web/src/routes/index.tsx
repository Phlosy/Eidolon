import { lazy, type ReactNode } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "../layouts/AppLayout";
import { PageNotFound } from "../components/common/states";
import { RouteBoundary } from "../components/common/route-boundary";
import { AuthGuard, GuestGuard } from "../features/auth/auth-guard";
import { LoginPage } from "../pages/auth/login-page";
import { RegisterPage } from "../pages/auth/register-page";
import { VerifyPage } from "../pages/auth/verify-page";

const DashboardPage = lazy(() =>
  import("../pages/dashboard/dashboard-page").then((module) => ({ default: module.DashboardPage })),
);
const OfficePage = lazy(() =>
  import("../pages/office/office-page").then((module) => ({ default: module.OfficePage })),
);
const EmployeesPage = lazy(() =>
  import("../pages/employees/employees-page").then((module) => ({ default: module.EmployeesPage })),
);
const EmployeeDetailPage = lazy(() =>
  import("../pages/employees/employee-detail-page").then((module) => ({
    default: module.EmployeeDetailPage,
  })),
);
const ProjectsPage = lazy(() =>
  import("../pages/projects/projects-page").then((module) => ({ default: module.ProjectsPage })),
);
const ProjectDetailPage = lazy(() =>
  import("../pages/projects/project-detail-page").then((module) => ({
    default: module.ProjectDetailPage,
  })),
);
const ReviewRoomPage = lazy(() =>
  import("../pages/projects/review-room-page").then((module) => ({
    default: module.ReviewRoomPage,
  })),
);
const DrivePage = lazy(() =>
  import("../pages/drive/drive-page").then((module) => ({ default: module.DrivePage })),
);
const RuntimePage = lazy(() =>
  import("../pages/runtime/runtime-page").then((module) => ({ default: module.RuntimePage })),
);
const SettingsPage = lazy(() =>
  import("../pages/settings/settings-page").then((module) => ({ default: module.SettingsPage })),
);

const route = (element: ReactNode) => <RouteBoundary>{element}</RouteBoundary>;

export const router = createBrowserRouter([
  {
    element: <GuestGuard />,
    children: [
      { path: "/auth/login", element: <LoginPage /> },
      { path: "/auth/register", element: <RegisterPage /> },
      { path: "/auth/verify", element: <VerifyPage /> },
    ],
  },
  {
    element: <AuthGuard />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: "/", element: route(<DashboardPage />) },
          { path: "/office", element: route(<OfficePage />) },
          { path: "/employees", element: route(<EmployeesPage />) },
          { path: "/employees/:id", element: route(<EmployeeDetailPage />) },
          { path: "/projects", element: route(<ProjectsPage />) },
          { path: "/projects/:id", element: route(<ProjectDetailPage />) },
          { path: "/projects/:id/reviews/:reviewId", element: route(<ReviewRoomPage />) },
          { path: "/drive", element: route(<DrivePage />) },
          { path: "/runtime", element: route(<RuntimePage />) },
          { path: "/artifacts", element: <Navigate to="/drive" replace /> },
          { path: "/artifacts/:id", element: <Navigate to="/drive" replace /> },
          { path: "/settings", element: route(<SettingsPage />) },
          { path: "*", element: <PageNotFound /> },
        ],
      },
    ],
  },
]);
