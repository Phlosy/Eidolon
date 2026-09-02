import { del, get, patch, post } from "./client";
import type {
  AuthState,
  Passkey,
  RegisterInput,
  RegistrationResult,
  UserSession,
  WebAuthnOptions,
} from "../types";

export const getAuthState = () => get<AuthState>("/auth/me");
export const registerAccount = (input: RegisterInput) =>
  post<RegistrationResult>("/auth/register", input);
export const verifyEmail = (token: string) => post<AuthState>("/auth/verify-email", { token });
export const loginWithPassword = (email: string, password: string) =>
  post<AuthState>("/auth/login", { email, password });
export const logout = () => post<void>("/auth/logout");
export const logoutAll = () => post<void>("/auth/logout-all");
export const getSessions = () => get<UserSession[]>("/auth/sessions");
export const revokeSession = (id: number) => del<void>(`/auth/sessions/${id}`);
export const getPasskeys = () => get<Passkey[]>("/auth/passkeys");
export const createPasskeyRegistrationOptions = (name: string) =>
  post<WebAuthnOptions>("/auth/passkeys/registration/options", { name });
export const verifyPasskeyRegistration = (challengeId: number, credential: unknown) =>
  post<Passkey>("/auth/passkeys/registration/verify", {
    challenge_id: challengeId,
    credential,
  });
export const createPasskeyAuthenticationOptions = () =>
  post<WebAuthnOptions>("/auth/passkeys/authentication/options");
export const verifyPasskeyAuthentication = (challengeId: number, credential: unknown) =>
  post<AuthState>("/auth/passkeys/authentication/verify", {
    challenge_id: challengeId,
    credential,
  });
export const renamePasskey = (id: number, name: string) =>
  patch<Passkey>(`/auth/passkeys/${id}`, { name });
export const removePasskey = (id: number) => del<void>(`/auth/passkeys/${id}`);
