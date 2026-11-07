import { del, get, getBlob, post } from "./client";
import type {
  Company,
  CostTrace,
  Decision,
  Employee,
  FactView,
  Institution,
  Ledger,
  LedgerVerify,
  Me,
  Mission,
  PersonProfile,
  Project,
  ProjectCosts,
  ProjectDetail,
  RunDetail,
  RunSummary,
  SessionInfo,
  TaskDetail,
  Trace,
} from "./types";

// -- auth ---------------------------------------------------------------------------

export const auth = {
  me: () => get<Me>("/auth/me"),
  login: (email: string, password: string) =>
    post<{ user_id: string; session_id: string }>("/auth/login", { email, password }),
  register: (body: { email: string; display_name: string; password: string; person_id?: string }) =>
    post<{ user_id: string; person_id: string }>("/auth/register", body),
  logout: () => post<void>("/auth/logout"),
  logoutAll: () => post<void>("/auth/logout-all"),
  sessions: () => get<SessionInfo[]>("/auth/sessions"),
  revokeSession: (id: string) => del<void>(`/auth/sessions/${id}`),
  changePassword: (old_password: string, new_password: string) =>
    post<void>("/auth/password", { old_password, new_password }),
};

// -- companies & people -----------------------------------------------------------------

export const companies = {
  mine: () => get<Company[]>("/companies"),
  one: (id: string) => get<Company>(`/companies/${id}`),
  institution: (id: string) => get<Institution>(`/companies/${id}/institution`),
  employees: (id: string) => get<Employee[]>(`/companies/${id}/employees`),
  person: (personId: string, withResume = true) =>
    get<PersonProfile>(`/persons/${encodeURIComponent(personId)}?with_resume=${withResume}`),
  create: (
    body: { name: string; slug: string; currency?: string; template_id?: string | null },
    key?: string,
  ) => post<{ company_id: string; owner_person_id: string }>("/companies", body, key),
  customize: (id: string, changes: Record<string, unknown>, reason: string) =>
    post<{ fact_id: string; version: number }>(`/companies/${id}/institution/customize`, {
      changes,
      reason,
    }),
  hire: (
    id: string,
    body: {
      person_id: string;
      role: string;
      name?: string;
      title?: string;
      persona?: Record<string, unknown>;
    },
  ) => post<{ employment_id: string; person_id: string }>(`/companies/${id}/employees`, body),
  endEmployment: (id: string, employmentId: string, reason: string) =>
    post<{ fact_id: string }>(`/companies/${id}/employments/${employmentId}/end`, { reason }),
};

// -- work ----------------------------------------------------------------------------

export const work = {
  missions: (companyId: string) => get<Mission[]>(`/companies/${companyId}/missions`),
  projects: (companyId: string) => get<Project[]>(`/companies/${companyId}/projects`),
  project: (id: string) => get<ProjectDetail>(`/projects/${id}`),
  task: (id: string) => get<TaskDetail>(`/tasks/${id}`),
  artifactContent: (id: string) => getBlob(`/artifacts/${id}/content`),
  proposeMission: (
    companyId: string,
    body: {
      title: string;
      goal: string;
      success_criteria: string[];
      constraints: string[];
      budget: number | null;
      deadline: string | null;
    },
    key?: string,
  ) => post<{ mission_id: string; fact_id: string }>(`/companies/${companyId}/missions`, body, key),
  deliver: (projectId: string, reason: string | null) =>
    post<Decision>(`/projects/${projectId}/deliver`, { reason }),
};

// -- runs ----------------------------------------------------------------------------

export const runs = {
  one: (id: string) => get<RunDetail>(`/runs/${id}`),
  ofCompany: (companyId: string, personId?: string) =>
    get<RunSummary[]>(
      `/companies/${companyId}/runs${personId ? `?person_id=${encodeURIComponent(personId)}` : ""}`,
    ),
  cancel: (id: string, reason: string) =>
    post<{ fact_id: string; run_id: string }>(`/runs/${id}/cancel`, { reason }),
};

// -- ledger ----------------------------------------------------------------------------

export const ledger = {
  balances: (companyId: string) => get<Ledger>(`/companies/${companyId}/ledger`),
  verify: (companyId: string) => get<LedgerVerify>(`/companies/${companyId}/ledger/verify`),
  projectCosts: (projectId: string) => get<ProjectCosts>(`/projects/${projectId}/costs`),
  costTrace: (ref: string) => get<CostTrace>(`/cost-trace/${ref}`),
};

// -- provenance ---------------------------------------------------------------------

export const provenance = {
  fact: (id: string) => get<FactView>(`/facts/${id}`),
  trace: (id: string, depth = 10) => get<Trace>(`/facts/${id}/trace?depth=${depth}`),
  bundleUrl: (companyId: string, correlation?: string, withLedger = false) =>
    `/api/v2/companies/${companyId}/bundle?${new URLSearchParams({
      ...(correlation ? { correlation } : {}),
      with_ledger: String(withLedger),
    })}`,
};

// -- decisions ------------------------------------------------------------------------

export const decisions = {
  pending: (companyId: string) => get<Decision[]>(`/companies/${companyId}/decisions`),
  all: (companyId: string) => get<Decision[]>(`/companies/${companyId}/decisions?decider=&status=`),
  one: (id: string) => get<Decision>(`/decisions/${id}`),
  open: (
    companyId: string,
    body: {
      gate: string;
      subject: Record<string, unknown>;
      action?: Record<string, unknown>;
      amount?: number;
    },
    key?: string,
  ) => post<Decision>(`/companies/${companyId}/decisions`, body, key),
  sign: (
    id: string,
    body: { verdict: string; reason?: string | null; action?: Record<string, unknown> },
  ) => post<Decision>(`/decisions/${id}/sign`, body),
};
