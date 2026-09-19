/* eslint-disable react-refresh/only-export-components */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, type ReactNode } from "react";
import { getAuthState } from "../../api/auth";
import { ApiError } from "../../api/client";
import { useEventStreamStore } from "../../stores/events";
import type { AuthState } from "../../types";

interface AuthContextValue {
  auth: AuthState | null;
  isLoading: boolean;
  error: Error | null;
  setAuth: (auth: AuthState | null) => void;
  refresh: () => Promise<unknown>;
}

const AuthContext = createContext<AuthContextValue | null>(null);
export const AUTH_QUERY_KEY = ["auth", "me"] as const;

/**
 * The session cookie itself is HttpOnly, but `eidolon_csrf` is written and cleared by
 * the same `create session` / `logout` calls (apps/server/app/services/auth.py), so its
 * presence is a reliable local hint that a session exists. Without it we skip the probe
 * entirely instead of letting the browser log a 401 for every guest page load.
 */
export function hasSessionHint(): boolean {
  if (typeof document === "undefined") return false;
  return document.cookie.split("; ").some((entry) => entry.startsWith("eidolon_csrf="));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const generation = useEventStreamStore((state) => state.generation);
  const reconcileIdentity = useCallback(
    (auth: AuthState | null) => {
      const previous = queryClient.getQueryData<AuthState | null>(AUTH_QUERY_KEY);
      if (previous?.user.id === auth?.user.id && previous?.company.id === auth?.company.id) return;

      // Reset the generation first: queued socket frames and mutation results from
      // the previous identity must be ignored, even before React unmounts its UI.
      useEventStreamStore.getState().reset();
      queryClient.removeQueries({
        predicate: ({ queryKey }) =>
          queryKey.length !== AUTH_QUERY_KEY.length ||
          queryKey.some((part, index) => part !== AUTH_QUERY_KEY[index]),
      });
      queryClient.getMutationCache().clear();
    },
    [queryClient],
  );
  const setAuth = useCallback(
    (auth: AuthState | null) => {
      // A pending /auth/me response must not replace a newer login or logout.
      void queryClient.cancelQueries({ queryKey: AUTH_QUERY_KEY, exact: true });
      reconcileIdentity(auth);
      queryClient.setQueryData(AUTH_QUERY_KEY, auth);
    },
    [queryClient, reconcileIdentity],
  );
  const query = useQuery({
    queryKey: AUTH_QUERY_KEY,
    queryFn: async ({ signal }) => {
      try {
        const auth = await getAuthState();
        if (!signal.aborted) reconcileIdentity(auth);
        return auth;
      } catch (error) {
        if (!signal.aborted && error instanceof ApiError && error.status === 401) {
          reconcileIdentity(null);
          queryClient.setQueryData(AUTH_QUERY_KEY, null);
        }
        throw error;
      }
    },
    enabled: hasSessionHint(),
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 1,
    staleTime: 30_000,
  });
  return (
    <AuthContext.Provider
      // Remount identity consumers so removed QueryObservers and local drafts
      // cannot carry their previous result into another user's first render.
      key={generation}
      value={{
        auth: query.data ?? null,
        isLoading: query.isLoading,
        error: query.error,
        setAuth,
        refresh: query.refetch,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
