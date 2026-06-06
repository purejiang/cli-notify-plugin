/**
 * crypto.ts — E2EE encryption using ECDH P-256 + AES-256-GCM.
 *
 * Protocol (Section 1.5 of the design spec):
 *   1. Generate ephemeral P-256 keypair
 *   2. ECDH with phone's public key → shared secret
 *   3. HKDF-SHA256 derive AES-256 key
 *   4. AES-256-GCM encrypt the plaintext
 *
 * This module mirrors com.clinotify.data.crypto.CryptoManager on Android.
 * Both sides use the same HKDF info string ("cli-notify-v1") and derive keys
 * identically, ensuring interoperability.
 */

import {
  createECDH,
  createCipheriv,
  createHmac,
  randomBytes,
} from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { EncryptedPayload, RelayConfig } from "./types.js";

// =========================================================================
// Constants
// =========================================================================

/** HKDF info string — must match Android CryptoManager.HKDF_INFO exactly. */
const HKDF_INFO = "cli-notify-v1";

/** AES-256 key length in bytes. */
const AES_KEY_LENGTH = 32;

/** AES-GCM IV length in bytes. */
const IV_LENGTH = 12;

/** AES-GCM authentication tag length in bytes. */
const AUTH_TAG_LENGTH = 16;

/** HKDF salt — 32 zero bytes (RFC 5869 "no salt"). */
const HKDF_SALT = Buffer.alloc(32, 0);

// =========================================================================
// Public Key Cache
// =========================================================================

/** Filename for the cached phone public key. */
const PUBKEY_CACHE_FILENAME = "phone-pubkey.txt";

/**
 * Attempts to retrieve the phone's E2EE public key.
 * Checks local cache first, then fetches from the relay.
 *
 * @param scriptsDir - Absolute path to the scripts/ directory.
 * @param config - Relay configuration (URL and token).
 * @returns Base64-encoded P-256 public key (uncompressed point), or null.
 */
export async function getPhonePublicKey(
  scriptsDir: string,
  config: RelayConfig,
): Promise<string | null> {
  // 1. Try local cache
  const cachePath = join(scriptsDir, PUBKEY_CACHE_FILENAME);
  try {
    const cached = readFileSync(cachePath, "utf-8").trim();
    if (cached) return cached;
  } catch {
    // Cache miss — continue to fetch
  }

  // 2. Fetch from relay
  try {
    const url = `${config.relayUrl}/pubkey?token=${encodeURIComponent(config.token)}`;
    const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
    if (res.ok) {
      const json = (await res.json()) as { publicKey?: string };
      const pubKey = json.publicKey;
      if (pubKey && typeof pubKey === "string") {
        try {
          writeFileSync(cachePath, pubKey, "utf-8");
        } catch {
          // Non-fatal: cache write failed, but we still have the key
        }
        return pubKey;
      }
    }
  } catch {
    // Relay unreachable — no encryption this cycle
  }

  return null;
}

// =========================================================================
// Encryption
// =========================================================================

/**
 * Encrypts a data payload for the phone using ECDH + AES-256-GCM.
 *
 * 1. Generates an ephemeral P-256 keypair.
 * 2. Computes the ECDH shared secret with the phone's public key.
 * 3. Derives an AES-256 key via HKDF-SHA256.
 * 4. Encrypts the JSON-serialized data with AES-256-GCM.
 *
 * @param data - Plaintext object to encrypt.
 * @param phonePubKeyBase64 - Phone's P-256 public key (base64, uncompressed point).
 * @returns EncryptedPayload containing ephemeral key, IV, and ciphertext.
 */
export function encryptPayload(
  data: Record<string, unknown>,
  phonePubKeyBase64: string,
): EncryptedPayload {
  // 1. Generate ephemeral P-256 keypair
  const ecdh = createECDH("prime256v1");
  const ephemeralPubKey = ecdh.generateKeys(); // uncompressed point (65 bytes)

  // 2. ECDH: compute shared secret
  const phonePubKey = Buffer.from(phonePubKeyBase64, "base64");
  const sharedSecret = ecdh.computeSecret(phonePubKey);

  // 3. HKDF-SHA256: derive AES-256 key
  const aesKey = hkdfExpand(sharedSecret, Buffer.from(HKDF_INFO, "utf-8"), AES_KEY_LENGTH);

  // 4. AES-256-GCM: encrypt
  const iv = randomBytes(IV_LENGTH);
  const cipher = createCipheriv("aes-256-gcm", aesKey, iv);
  const plaintext = Buffer.from(JSON.stringify(data), "utf-8");
  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const authTag = cipher.getAuthTag();

  // Pack: ciphertext || authTag (Android splits them apart before decrypting)
  const ciphertextWithTag = Buffer.concat([encrypted, authTag]);

  return {
    ephemeralKey: ephemeralPubKey.toString("base64"),
    iv: iv.toString("base64"),
    ciphertext: ciphertextWithTag.toString("base64"),
  };
}

// =========================================================================
// HKDF-SHA256 (RFC 5869)
// =========================================================================

/**
 * HKDF-SHA256: extract-then-expand.
 *
 * Extract:  PRK = HMAC-SHA256(salt=0x00*32, IKM)
 * Expand:   OKM = T(1) || T(2) || ...
 *           T(0) = empty
 *           T(i) = HMAC-SHA256(PRK, T(i-1) || info || counter)
 *
 * This implementation exactly matches the Android CryptoManager.hkdfExpand
 * to ensure both sides derive the same AES key.
 *
 * @param ikm - Input keying material (the ECDH shared secret).
 * @param info - Context/application-specific info string.
 * @param length - Desired output key length in bytes.
 */
function hkdfExpand(ikm: Buffer, info: Buffer, length: number): Buffer {
  // Extract: PRK = HMAC-SHA256(salt, IKM)
  const prk = createHmac("sha256", HKDF_SALT).update(ikm).digest();

  // Expand
  const okm = Buffer.alloc(length);
  let t = Buffer.alloc(0);
  let offset = 0;
  let counter = 1;

  while (offset < length) {
    const hmac = createHmac("sha256", prk);
    hmac.update(t);
    hmac.update(info);
    hmac.update(Buffer.from([counter]));
    t = hmac.digest();

    const copyLen = Math.min(t.length, length - offset);
    t.copy(okm, offset, 0, copyLen);
    offset += copyLen;
    counter++;
  }

  return okm;
}
