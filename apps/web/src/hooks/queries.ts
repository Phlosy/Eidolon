/** react-query hooks over the v2 API. Keys are `[entity, id]` so a mutation can
 * invalidate by prefix. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { companies, decisions, ledger, provenance, runs, work } from "../api";
import { AUTH_QUERY_KEY } from "../features/auth/auth-context";

export const keys = {
  company: (id: string) => ["company", id] as const,
  institution: (id: string) => ["institution", id] as const,
  employees: (id: string) => ["employees", id] as const,
  person: (id: string) => ["person", id] as const,
  missions: (id: string) => ["missions", id] as const,
  projects: (id: string) => ["projects", id] as const,
  project: (id: string) => ["project", id] as const,
  task: (id: string) => ["task", id] as const,
  runs: (id: string, person?: string) => ["runs", id, person ?? ""] as const,
  run: (id: string) => ["run", id] as const,
  ledger: (id: string) => ["ledger", id] as const,
  ledgerVerify: (id: string) => ["ledger-verify", id] as const,
  projectCosts: (id: string) => ["project-costs", id] as const,
  costTrace: (ref: string) => ["cost-trace", ref] as const,
  trace: (id: string) => ["trace", id] as const,
  decisions: (id: string) => ["decisions", id] as const,
  report: (kind: string, id: string, lang: string) => ["report", kind, id, lang] as const,
};

export const useCompanyQuery = (id: string) =>
  useQuery({ queryKey: keys.company(id), queryFn: () => companies.one(id) });
export const useInstitution = (id: string) =>
  useQuery({ queryKey: keys.institution(id), queryFn: () => companies.institution(id) });
export const useEmployees = (id: string) =>
  useQuery({ queryKey: keys.employees(id), queryFn: () => companies.employees(id) });
export const usePerson = (personId: string) =>
  useQuery({ queryKey: keys.person(personId), queryFn: () => companies.person(personId) });
export const useMissions = (id: string) =>
  useQuery({ queryKey: keys.missions(id), queryFn: () => work.missions(id) });
export const useProjects = (id: string) =>
  useQuery({ queryKey: keys.projects(id), queryFn: () => work.projects(id) });
export const useProject = (id: string) =>
  useQuery({
    queryKey: keys.project(id),
    queryFn: () => work.project(id),
    refetchInterval: 10_000,
  });
export const useTask = (id: string) =>
  useQuery({ queryKey: keys.task(id), queryFn: () => work.task(id) });
export const useRuns = (companyId: string, personId?: string) =>
  useQuery({
    queryKey: keys.runs(companyId, personId),
    queryFn: () => runs.ofCompany(companyId, personId),
    refetchInterval: 10_000,
  });
export const useRun = (id: string) =>
  useQuery({ queryKey: keys.run(id), queryFn: () => runs.one(id), refetchInterval: 5_000 });
export const useLedger = (id: string) =>
  useQuery({ queryKey: keys.ledger(id), queryFn: () => ledger.balances(id) });
export const useLedgerVerify = (id: string) =>
  useQuery({ queryKey: keys.ledgerVerify(id), queryFn: () => ledger.verify(id) });
export const useProjectReport = (id: string, lang: string, enabled = true) =>
  useQuery({
    queryKey: keys.report("project", id, lang),
    queryFn: () => work.report(id, lang),
    enabled,
    refetchInterval: 15_000,
  });
export const useProjectCosts = (id: string) =>
  useQuery({ queryKey: keys.projectCosts(id), queryFn: () => ledger.projectCosts(id) });
export const useCostTrace = (ref: string | null) =>
  useQuery({
    queryKey: keys.costTrace(ref ?? ""),
    queryFn: () => ledger.costTrace(ref!),
    enabled: Boolean(ref),
  });
export const useTrace = (factId: string | null) =>
  useQuery({
    queryKey: keys.trace(factId ?? ""),
    queryFn: () => provenance.trace(factId!),
    enabled: Boolean(factId),
  });
export const useDecisions = (id: string) =>
  useQuery({
    queryKey: keys.decisions(id),
    queryFn: () => decisions.pending(id),
    refetchInterval: 10_000,
  });

/** Any command: invalidate everything but the session, since facts ripple. */
export function useCommand<TArgs, TResult>(fn: (args: TArgs) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () =>
      client.invalidateQueries({
        predicate: ({ queryKey }) => queryKey[0] !== AUTH_QUERY_KEY[0],
      }),
  });
}

/** Commands that change who the user is in which company refresh the session too. */
export function useIdentityCommand<TArgs, TResult>(fn: (args: TArgs) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => client.invalidateQueries() });
}
