// KAN-196 (A-13): the browser side of passkeys. The server (py_webauthn)
// speaks the WebAuthn JSON form — challenge, user id and credential ids as
// base64url strings; the browser API wants ArrayBuffers and gives them back.
// Modern browsers convert both ways (`parseCreationOptionsFromJSON`,
// `credential.toJSON()`); this module falls back to the same conversion by
// hand where they do not, so one code path serves every supported browser.

type JsonCredential = Record<string, unknown>

function base64UrlToBuffer(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (value.length % 4)) % 4)
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

function bufferToBase64Url(value: ArrayBuffer): string {
  let binary = ''
  for (const byte of new Uint8Array(value)) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function passkeysSupported(): boolean {
  return typeof window !== 'undefined' && typeof window.PublicKeyCredential === 'function'
}

type PkcStatic = typeof PublicKeyCredential & {
  parseCreationOptionsFromJSON?: (json: unknown) => PublicKeyCredentialCreationOptions
  parseRequestOptionsFromJSON?: (json: unknown) => PublicKeyCredentialRequestOptions
}

function withBuffers<T extends Record<string, unknown>>(options: T): T {
  const out: Record<string, unknown> = { ...options, challenge: base64UrlToBuffer(options.challenge as string) }
  const user = options.user as Record<string, unknown> | undefined
  if (user) out.user = { ...user, id: base64UrlToBuffer(user.id as string) }
  for (const key of ['excludeCredentials', 'allowCredentials'] as const) {
    const list = options[key] as Array<Record<string, unknown>> | undefined
    if (list) out[key] = list.map((c) => ({ ...c, id: base64UrlToBuffer(c.id as string) }))
  }
  return out as T
}

function credentialToJson(credential: PublicKeyCredential): JsonCredential {
  const asJson = (credential as PublicKeyCredential & { toJSON?: () => JsonCredential }).toJSON
  if (typeof asJson === 'function') return asJson.call(credential)
  const response = credential.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse
  const body: Record<string, unknown> = { clientDataJSON: bufferToBase64Url(response.clientDataJSON) }
  if ('attestationObject' in response && response.attestationObject) {
    body.attestationObject = bufferToBase64Url(response.attestationObject)
    body.transports = typeof response.getTransports === 'function' ? response.getTransports() : []
  }
  if ('authenticatorData' in response && response.authenticatorData) {
    body.authenticatorData = bufferToBase64Url(response.authenticatorData)
    body.signature = bufferToBase64Url(response.signature)
    body.userHandle = response.userHandle ? bufferToBase64Url(response.userHandle) : null
  }
  return {
    id: credential.id,
    rawId: bufferToBase64Url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? null,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: body,
  }
}

/** Registers a passkey from the server's creation options (JSON string);
 * resolves with the credential in the JSON form the server verifies. */
export async function createPasskey(optionsJson: string): Promise<JsonCredential> {
  const json = JSON.parse(optionsJson) as Record<string, unknown>
  const Pkc = window.PublicKeyCredential as PkcStatic
  const publicKey = Pkc.parseCreationOptionsFromJSON
    ? Pkc.parseCreationOptionsFromJSON(json)
    : (withBuffers(json) as unknown as PublicKeyCredentialCreationOptions)
  const credential = await navigator.credentials.create({ publicKey })
  if (!credential) throw new Error('passkey_cancelled')
  return credentialToJson(credential as PublicKeyCredential)
}

/** Signs the server's request options (JSON string) with a passkey. */
export async function assertPasskey(optionsJson: string): Promise<JsonCredential> {
  const json = JSON.parse(optionsJson) as Record<string, unknown>
  const Pkc = window.PublicKeyCredential as PkcStatic
  const publicKey = Pkc.parseRequestOptionsFromJSON
    ? Pkc.parseRequestOptionsFromJSON(json)
    : (withBuffers(json) as unknown as PublicKeyCredentialRequestOptions)
  const credential = await navigator.credentials.get({ publicKey })
  if (!credential) throw new Error('passkey_cancelled')
  return credentialToJson(credential as PublicKeyCredential)
}
