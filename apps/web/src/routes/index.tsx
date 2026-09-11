import { lazy, type ReactNode } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "../layouts/AppLayout";
import { PageNotFound } from "../components/common/states";
import { RouteBoundary } from "../components/common/route-boundary";
import { AuthGuard, GuestGuard } from "../features/auth/auth-guard";
import { AuthShellLayout } from "../features/auth/auth-shell";
import { LoginPage } from "../pages/auth/login-page";
import { RegisterPage } from "../pages/auth/register-page";
import { VerifyPage } from "../pages/auth/verify-page";
import { ConfirmActionPage } from "../pages/auth/confirm-action-page";

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
const TalentRosterPage = lazy(() =>
  import("../pages/talent-roster/talent-roster-page").then((module) => ({
    default: module.TalentRosterPage,
  })),
);
const MarketPage = lazy(() =>
  import("../pages/market/market-page").then((module) => ({ default: module.MarketPage })),
);
const MarketListingPage = lazy(() =>
  import("../pages/market/market-listing-page").then((module) => ({
    default: module.MarketListingPage,
  })),
);
const EconomyPage = lazy(() =>
  import("../pages/economy/economy-page").then((module) => ({ default: module.EconomyPage })),
);
const WorkOrdersPage = lazy(() =>
  import("../pages/work-orders/work-orders-page").then((module) => ({
    default: module.WorkOrdersPage,
  })),
);
const ContractsPage = lazy(() =>
  import("../pages/contracts/contracts-page").then((module) => ({ default: module.ContractsPage })),
);
const CultivationPage = lazy(() =>
  import("../pages/cultivation/cultivation-page").then((module) => ({
    default: module.CultivationPage,
  })),
);
const CultivationDetailPage = lazy(() =>
  import("../pages/cultivation/cultivation-detail-page").then((module) => ({
    default: module.CultivationDetailPage,
  })),
);
const PositionsPage = lazy(() =>
  import("../pages/positions/positions-page").then((module) => ({
    default: module.PositionsPage,
  })),
);
const PositionDetailPage = lazy(() =>
  import("../pages/positions/position-detail-page").then((module) => ({
    default: module.PositionDetailPage,
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
const KnowledgePage = lazy(() =>
  import("../pages/knowledge/knowledge-page").then((module) => ({
    default: module.KnowledgePage,
  })),
);
const RuntimePage = lazy(() =>
  import("../pages/runtime/runtime-page").then((module) => ({ default: module.RuntimePage })),
);
const SettingsLayout = lazy(() =>
  import("../pages/settings/settings-layout").then((module) => ({
    default: module.SettingsLayout,
  })),
);
const ProfileSection = lazy(() =>
  import("../pages/settings/profile-section").then((module) => ({
    default: module.ProfileSection,
  })),
);
const SecuritySection = lazy(() =>
  import("../pages/settings/security-section").then((module) => ({
    default: module.SecuritySection,
  })),
);
const PreferencesSection = lazy(() =>
  import("../pages/settings/preferences-section").then((module) => ({
    default: module.PreferencesSection,
  })),
);
const TutorialSection = lazy(() =>
  import("../pages/settings/tutorial-section").then((module) => ({
    default: module.TutorialSection,
  })),
);
const ProvidersSection = lazy(() =>
  import("../pages/settings/providers-section").then((module) => ({
    default: module.ProvidersSection,
  })),
);
const GitSection = lazy(() =>
  import("../pages/settings/git-section").then((module) => ({ default: module.GitSection })),
);
const RuntimeSection = lazy(() =>
  import("../pages/settings/runtime-section").then((module) => ({
    default: module.RuntimeSection,
  })),
);
const AboutSection = lazy(() =>
  import("../pages/settings/about-section").then((module) => ({ default: module.AboutSection })),
);

const route = (element: ReactNode) => <RouteBoundary>{element}</RouteBoundary>;

export const router = createBrowserRouter([
  {
    element: <GuestGuard />,
    children: [
      {
        // 办公室由 layout route 持有，登录/注册/验证之间切换不会重启 Phaser。
        element: <AuthShellLayout />,
        children: [
          { path: "/auth/login", element: <LoginPage /> },
          { path: "/auth/register", element: <RegisterPage /> },
          { path: "/auth/verify", element: <VerifyPage /> },
        ],
      },
    ],
  },
  {
    // 账户安全操作确认（改邮箱/改密码/注销）：点邮件链接时可能已登录也可能
    // 没有，不能进 GuestGuard 也不能要求 AuthGuard
    path: "/auth/confirm-action",
    element: <ConfirmActionPage />,
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
          { path: "/talent-roster", element: route(<TalentRosterPage />) },
          { path: "/market", element: route(<MarketPage />) },
          { path: "/economy", element: route(<EconomyPage />) },
          { path: "/work-orders", element: route(<WorkOrdersPage />) },
          { path: "/contracts", element: route(<ContractsPage />) },
          { path: "/market/:listingId", element: route(<MarketListingPage />) },
          { path: "/cultivation", element: route(<CultivationPage />) },
          { path: "/cultivation/:id", element: route(<CultivationDetailPage />) },
          { path: "/positions", element: route(<PositionsPage />) },
          { path: "/positions/:id", element: route(<PositionDetailPage />) },
          { path: "/projects", element: route(<ProjectsPage />) },
          { path: "/projects/:id", element: route(<ProjectDetailPage />) },
          { path: "/projects/:id/reviews/:reviewId", element: route(<ReviewRoomPage />) },
          { path: "/drive", element: route(<DrivePage />) },
          { path: "/knowledge", element: route(<KnowledgePage />) },
          { path: "/runtime", element: route(<RuntimePage />) },
          { path: "/artifacts", element: <Navigate to="/drive" replace /> },
          { path: "/artifacts/:id", element: <Navigate to="/drive" replace /> },
          {
            path: "/settings",
            element: route(<SettingsLayout />),
            children: [
              { index: true, element: <Navigate to="/settings/profile" replace /> },
              { path: "profile", element: route(<ProfileSection />) },
              { path: "security", element: route(<SecuritySection />) },
              { path: "preferences", element: route(<PreferencesSection />) },
              { path: "tutorial", element: route(<TutorialSection />) },
              { path: "providers", element: route(<ProvidersSection />) },
              { path: "git", element: route(<GitSection />) },
              { path: "runtime", element: route(<RuntimeSection />) },
              { path: "about", element: route(<AboutSection />) },
            ],
          },
          { path: "*", element: <PageNotFound /> },
        ],
      },
    ],
  },
]);
