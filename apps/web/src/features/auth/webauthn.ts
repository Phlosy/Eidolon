import {
  createPasskeyAuthenticationOptions,
  createPasskeyRegistrationOptions,
  verifyPasskeyAuthentication,
  verifyPasskeyRegistration,
} from "../../api/auth";

export function base64UrlToBytes(value: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(value.replace(/-/g, "+").replace(/_/g, "/") + padding);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function bytesToBase64Url(value: ArrayBuffer): string {
  const binary = Array.from(new Uint8Array(value), (byte) => String.fromCharCode(byte)).join("");
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function responseToJSON(response: AuthenticatorResponse): Record<string, unknown> {
  const base = { clientDataJSON: bytesToBase64Url(response.clientDataJSON) };
  if ("attestationObject" in response) {
    const attestation = response as AuthenticatorAttestationResponse;
    return {
      ...base,
      attestationObject: bytesToBase64Url(attestation.attestationObject),
      transports: attestation.getTransports?.() ?? [],
    };
  }
  const assertion = response as AuthenticatorAssertionResponse;
  return {
    ...base,
    authenticatorData: bytesToBase64Url(assertion.authenticatorData),
    signature: bytesToBase64Url(assertion.signature),
    userHandle: assertion.userHandle ? bytesToBase64Url(assertion.userHandle) : null,
  };
}

export function credentialToJSON(credential: PublicKeyCredential): Record<string, unknown> {
  const native = credential as PublicKeyCredential & { toJSON?: () => Record<string, unknown> };
  if (native.toJSON) return native.toJSON();
  return {
    id: credential.id,
    type: credential.type,
    rawId: bytesToBase64Url(credential.rawId),
    response: responseToJSON(credential.response),
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

function creationOptions(json: Record<string, unknown>): PublicKeyCredentialCreationOptions {
  const parser = PublicKeyCredential as typeof PublicKeyCredential & {
    parseCreationOptionsFromJSON?: (value: unknown) => PublicKeyCredentialCreationOptions;
  };
  if (parser.parseCreationOptionsFromJSON) return parser.parseCreationOptionsFromJSON(json);
  const value = json as unknown as {
    challenge: string;
    user: PublicKeyCredentialUserEntity & { id: string };
    excludeCredentials?: Array<Omit<PublicKeyCredentialDescriptor, "id"> & { id: string }>;
  };
  return {
    ...(json as unknown as PublicKeyCredentialCreationOptions),
    challenge: base64UrlToBytes(value.challenge),
    user: { ...value.user, id: base64UrlToBytes(value.user.id) },
    excludeCredentials: value.excludeCredentials?.map((item) => ({
      ...item,
      id: base64UrlToBytes(item.id),
    })),
  };
}

function requestOptions(json: Record<string, unknown>): PublicKeyCredentialRequestOptions {
  const parser = PublicKeyCredential as typeof PublicKeyCredential & {
    parseRequestOptionsFromJSON?: (value: unknown) => PublicKeyCredentialRequestOptions;
  };
  if (parser.parseRequestOptionsFromJSON) return parser.parseRequestOptionsFromJSON(json);
  const value = json as unknown as {
    challenge: string;
    allowCredentials?: Array<Omit<PublicKeyCredentialDescriptor, "id"> & { id: string }>;
  };
  return {
    ...(json as unknown as PublicKeyCredentialRequestOptions),
    challenge: base64UrlToBytes(value.challenge),
    allowCredentials: value.allowCredentials?.map((item) => ({
      ...item,
      id: base64UrlToBytes(item.id),
    })),
  };
}

export function passkeysAvailable(): boolean {
  return Boolean(window.isSecureContext && navigator.credentials && window.PublicKeyCredential);
}

export async function registerPasskey(name: string) {
  const options = await createPasskeyRegistrationOptions(name);
  const credential = (await navigator.credentials.create({
    publicKey: creationOptions(options.public_key),
  })) as PublicKeyCredential | null;
  if (!credential) throw new Error("Passkey creation was cancelled");
  return verifyPasskeyRegistration(options.challenge_id, credentialToJSON(credential));
}

export async function signInWithPasskey() {
  const options = await createPasskeyAuthenticationOptions();
  const credential = (await navigator.credentials.get({
    publicKey: requestOptions(options.public_key),
  })) as PublicKeyCredential | null;
  if (!credential) throw new Error("Passkey sign-in was cancelled");
  return verifyPasskeyAuthentication(options.challenge_id, credentialToJSON(credential));
}
