/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Membership } from "../../api/types";
import { useAuth } from "../auth/auth-context";

const STORAGE_KEY = "eidolon-company";

interface CompanyContextValue {
  /** The user's memberships (one per company they are employed by). */
  memberships: Membership[];
  /** The company the UI is looking at; null when the user belongs to none. */
  current: Membership | null;
  select: (companyId: string) => void;
}

const CompanyContext = createContext<CompanyContextValue | null>(null);

function remembered(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function CompanyProvider({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const memberships = useMemo(() => me?.companies ?? [], [me]);
  const [selected, setSelected] = useState<string | null>(remembered);
  const current = memberships.find((m) => m.company_id === selected) ?? memberships[0] ?? null;
  useEffect(() => {
    if (current && current.company_id !== selected) setSelected(current.company_id);
  }, [current, selected]);
  const select = useCallback((companyId: string) => {
    setSelected(companyId);
    try {
      window.localStorage.setItem(STORAGE_KEY, companyId);
    } catch {
      // storage unavailable: the choice lives for this page only
    }
  }, []);
  return (
    <CompanyContext.Provider value={{ memberships, current, select }}>
      {children}
    </CompanyContext.Provider>
  );
}

export function useCompany(): CompanyContextValue {
  const value = useContext(CompanyContext);
  if (!value) throw new Error("useCompany must be used within CompanyProvider");
  return value;
}

/** The current membership, or throw: pages under the company guard always have one. */
export function useCurrentCompany(): Membership {
  const { current } = useCompany();
  if (!current) throw new Error("no current company");
  return current;
}
