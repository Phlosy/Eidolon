import { describe, expect, it } from "vitest";
import { base64UrlToBytes, bytesToBase64Url, credentialToJSON } from "./webauthn";

describe("webauthn encoding", () => {
  it("round-trips base64url without padding", () => {
    const bytes = new Uint8Array([0, 1, 2, 250, 251, 252, 253, 254, 255]);
    const text = bytesToBase64Url(bytes.buffer);
    expect(text).not.toMatch(/[+/=]/);
    expect(Array.from(base64UrlToBytes(text))).toEqual(Array.from(bytes));
  });

  it("serialises a credential the way the server's webauthn library expects", () => {
    const clientDataJSON = new TextEncoder().encode('{"type":"webauthn.get"}').buffer;
    const credential = {
      id: "cred-1",
      type: "public-key",
      rawId: new Uint8Array([1, 2, 3]).buffer,
      response: {
        clientDataJSON,
        authenticatorData: new Uint8Array([9]).buffer,
        signature: new Uint8Array([8]).buffer,
        userHandle: null,
      },
      getClientExtensionResults: () => ({}),
    } as unknown as PublicKeyCredential;
    const json = credentialToJSON(credential) as {
      id: string;
      rawId: string;
      response: { clientDataJSON: string; signature: string; userHandle: null };
    };
    expect(json.id).toBe("cred-1");
    expect(json.rawId).toBe("AQID");
    expect(json.response.signature).toBe("CA");
    expect(json.response.userHandle).toBeNull();
    expect(atob(json.response.clientDataJSON.replace(/-/g, "+").replace(/_/g, "/"))).toContain(
      "webauthn.get",
    );
  });
});
