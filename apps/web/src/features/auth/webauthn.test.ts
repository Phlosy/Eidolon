import { describe, expect, it } from "vitest";
import { base64UrlToBytes, credentialToJSON } from "./webauthn";

describe("webauthn browser bridge", () => {
  it("decodes base64url values without padding", () => {
    expect([...base64UrlToBytes("AQID-_8")]).toEqual([1, 2, 3, 251, 255]);
  });

  it("serializes credential buffers for the backend", () => {
    const credential = {
      id: "credential",
      type: "public-key",
      rawId: new Uint8Array([1, 2, 3]).buffer,
      response: {
        clientDataJSON: new Uint8Array([4]).buffer,
        authenticatorData: new Uint8Array([5]).buffer,
        signature: new Uint8Array([6]).buffer,
        userHandle: null,
      },
      getClientExtensionResults: () => ({ credProps: { rk: true } }),
    } as unknown as PublicKeyCredential;

    expect(credentialToJSON(credential)).toMatchObject({
      id: "credential",
      rawId: "AQID",
      response: { clientDataJSON: "BA", authenticatorData: "BQ", signature: "Bg" },
    });
  });
});
