import { createBrowserRouter } from "react-router-dom";
import { AppLayout } from "../layouts/AppLayout";
import { DashboardPage } from "../pages/dashboard/dashboard-page";
import { OfficePage } from "../pages/office/office-page";
import { EmployeesPage } from "../pages/employees/employees-page";
import { EmployeeDetailPage } from "../pages/employees/employee-detail-page";
import { ProjectsPage } from "../pages/projects/projects-page";
import { ProjectDetailPage } from "../pages/projects/project-detail-page";
import { ArtifactsPage } from "../pages/artifacts/artifacts-page";
import { ArtifactDetailPage } from "../pages/artifacts/artifact-detail-page";
import { SettingsPage } from "../pages/settings/settings-page";
import { PageNotFound } from "../components/common/states";

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <DashboardPage /> },
      { path: "/office", element: <OfficePage /> },
      { path: "/employees", element: <EmployeesPage /> },
      { path: "/employees/:id", element: <EmployeeDetailPage /> },
      { path: "/projects", element: <ProjectsPage /> },
      { path: "/projects/:id", element: <ProjectDetailPage /> },
      { path: "/artifacts", element: <ArtifactsPage /> },
      { path: "/artifacts/:id", element: <ArtifactDetailPage /> },
      { path: "/settings", element: <SettingsPage /> },
      { path: "*", element: <PageNotFound /> },
    ],
  },
]);
