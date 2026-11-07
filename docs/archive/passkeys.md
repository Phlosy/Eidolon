# Passkeys / WebAuthn

Eidolon uses the standard WebAuthn ceremony through the mature Python `webauthn` library. There are no Apple-specific or Google-specific authentication protocols. Compatible browsers choose iCloud Keychain, Google Password Manager, Windows Hello, Android Credential Manager, or a hardware security key.

## Stored data

`PasskeyCredential` stores the credential id, public key, signature counter, transports, device type, backup state, display name and last-used time. A private key is never received or stored by Eidolon; it remains in the user's authenticator.

## Ceremonies

- Registration: authenticated browser requests options, calls `navigator.credentials.create()`, then the server verifies the response and stores the public credential.
- Authentication: the login page requests discoverable-credential options without an email, calls `navigator.credentials.get()`, and the server verifies the assertion before rotating the session.
- Challenges expire after `EIDOLON_WEBAUTHN_CHALLENGE_TTL_MINUTES` and become unusable after the first successful verification.

Configure the relying party explicitly:

```dotenv
EIDOLON_WEBAUTHN_RP_ID=login.example.com
EIDOLON_WEBAUTHN_RP_NAME=Eidolon
EIDOLON_WEBAUTHN_ORIGIN=https://login.example.com
EIDOLON_COOKIE_SECURE=true
```

The RP ID must match the deployed domain and the origin must match the browser origin exactly. Production WebAuthn requires a secure HTTPS context; localhost is the development exception.

References: [py_webauthn registration](https://duo-labs.github.io/py_webauthn/registration.html), [MDN Web Authentication API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API).
