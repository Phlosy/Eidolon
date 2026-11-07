import { lazy, type ReactNode } from "react";
import { createBrowserRouter } from "react-router-dom";
import { PageNotFound } from "../components/common/states";
import { RouteBoundary } from "../components/common/route-boundary";
import { AppShell } from "../components/layout/app-shell";
import { AuthGuard, GuestGuard } from "../features/auth/auth-guard";
import { AuthShellLayout } from "../features/auth/auth-shell";
import { CompanyGuard } from "../features/company/company-guard";
import { LoginPage } from "../pages/auth/login-page";
import { RegisterPage } from "../pages/auth/register-page";

const page = <T extends Record<string, unknown>>(
  loader: () => Promise<T>,
  name: keyof T & string,
) => lazy(() => loader().then((module) => ({ default: module[name] as React.ComponentType })));

const OnboardingPage = page(() => import("../pages/onboarding/onboarding-page"), "OnboardingPage");
const DashboardPage = page(() => import("../pages/dashboard/dashboard-page"), "DashboardPage");
const ProjectsPage = page(() => import("../pages/projects/projects-page"), "ProjectsPage");
const ProjectPage = page(() => import("../pages/projects/project-page"), "ProjectPage");
const EmployeesPage = page(() => import("../pages/employees/employees-page"), "EmployeesPage");
const PersonPage = page(() => import("../pages/employees/person-page"), "PersonPage");
const RunPage = page(() => import("../pages/runs/run-page"), "RunPage");
const DrivePage = page(() => import("../pages/drive/drive-page"), "DrivePage");
const EconomyPage = page(() => import("../pages/economy/economy-page"), "EconomyPage");
const SettingsPage = page(() => import("../pages/settings/settings-page"), "SettingsPage");

const route = (element: ReactNode) => <RouteBoundary>{element}</RouteBoundary>;

export const router = createBrowserRouter([
  {
    element: <GuestGuard />,
    children: [
      {
        element: <AuthShellLayout />,
        children: [
          { path: "/auth/login", element: <LoginPage /> },
          { path: "/auth/register", element: <RegisterPage /> },
        ],
      },
    ],
  },
  {
    element: <AuthGuard />,
    children: [
      { path: "/onboarding", element: route(<OnboardingPage />) },
      {
        element: <CompanyGuard />,
        children: [
          {
            element: <AppShell />,
            children: [
              { path: "/", element: route(<DashboardPage />) },
              { path: "/projects", element: route(<ProjectsPage />) },
              { path: "/projects/:id", element: route(<ProjectPage />) },
              { path: "/employees", element: route(<EmployeesPage />) },
              { path: "/employees/:personId", element: route(<PersonPage />) },
              { path: "/runs/:id", element: route(<RunPage />) },
              { path: "/drive", element: route(<DrivePage />) },
              { path: "/economy", element: route(<EconomyPage />) },
              { path: "/settings", element: route(<SettingsPage />) },
              { path: "/settings/:section", element: route(<SettingsPage />) },
              { path: "*", element: <PageNotFound /> },
            ],
          },
        ],
      },
    ],
  },
]);
