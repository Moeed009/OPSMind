import { api } from './api';

let cachedRsaKey: CryptoKey | null = null;

function pemToArrayBuffer(pem: string): ArrayBuffer {
  const b64 = pem
    .replace(/-----BEGIN[^-]+-----/g, '')
    .replace(/-----END[^-]+-----/g, '')
    .replace(/\s+/g, '');
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

async function getPublicKey(): Promise<CryptoKey> {
  if (cachedRsaKey) {
    return cachedRsaKey;
  }
  const res = await api<{ public_key: string }>('/auth/public-key');
  const spkiBuffer = pemToArrayBuffer(res.public_key);
  cachedRsaKey = await window.crypto.subtle.importKey(
    'spki',
    spkiBuffer,
    {
      name: 'RSA-OAEP',
      hash: 'SHA-256',
    },
    false,
    ['encrypt']
  );
  return cachedRsaKey;
}

/**
 * Encrypts authentication credentials using hybrid RSA-OAEP + AES-256-GCM
 * so that plaintext passwords and emails never appear in browser Network inspector.
 */
export async function encryptAuthPayload(data: Record<string, any>): Promise<string> {
  const rsaKey = await getPublicKey();

  const aesKey = await window.crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 },
    true,
    ['encrypt']
  );

  const rawAesKey = await window.crypto.subtle.exportKey('raw', aesKey);
  const encryptedAesKey = await window.crypto.subtle.encrypt(
    { name: 'RSA-OAEP' },
    rsaKey,
    rawAesKey
  );

  const iv = window.crypto.getRandomValues(new Uint8Array(12));
  const encodedPayload = new TextEncoder().encode(
    JSON.stringify({
      ...data,
      client_timestamp: Date.now(),
    })
  );

  const ciphertext = await window.crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    aesKey,
    encodedPayload
  );

  const packet = {
    k: arrayBufferToBase64(encryptedAesKey),
    iv: arrayBufferToBase64(iv.buffer),
    d: arrayBufferToBase64(ciphertext),
  };

  return btoa(JSON.stringify(packet));
}
