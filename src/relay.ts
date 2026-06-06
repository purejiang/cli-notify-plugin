/**
 * relay.ts — HTTP transport to Cloud Relay with retry logic.
 *
 * Posts event envelopes to the Cloud Relay's /hook/relay endpoint.
 * Handles transient failures with exponential backoff and jitter.
 * Never blocks Claude Code — notification delivery is best-effort.
 */

import type { Envelope, RetryConfig } from "./types.js";

// =========================================================================
// Retry Configuration
// =========================================================================

/**
 * Default retry policy for HTTP requests to the relay.
 *
 * - 3 retries with exponential backoff (1s → 2s → 4s)
 * - Maximum delay capped at 30 seconds
 * - Full jitter to avoid thundering herd
 */
const DEFAULT_RETRY_CONFIG: RetryConfig = {
  maxRetries: 3,
  baseDelayMs: 1000,
  maxDelayMs: 30000,
};

/** HTTP status codes that should NOT be retried. */
const NON_RETRYABLE_STATUS = new Set([400, 401, 403, 404, 405, 409, 410, 422]);

// =========================================================================
// Main Export
// =========================================================================

/**
 * POSTs an event envelope to the Cloud Relay.
 *
 * Retry strategy:
 *   - Network errors (fetch failed):    Retry with exponential backoff
 *   - 5xx errors (server fault):        Retry with exponential backoff
 *   - 429 (rate limit):                 Retry, using Retry-After header if present
 *   - 4xx errors (client fault):        Do NOT retry, log error
 *   - Timeout:                          Retry
 *
 * @param relayUrl - Base URL of the Cloud Relay (e.g. "http://host:8765").
 * @param token - JWT authentication token.
 * @param envelope - Full protocol envelope to send.
 * @param retryConfig - Optional retry policy override.
 * @returns true if the envelope was delivered successfully, false otherwise.
 */
export async function postToRelay(
  relayUrl: string,
  token: string,
  envelope: Envelope,
  retryConfig: RetryConfig = DEFAULT_RETRY_CONFIG,
): Promise<boolean> {
  const url = `${relayUrl}/hook/relay?token=${encodeURIComponent(token)}`;
  const body = JSON.stringify(envelope);

  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= retryConfig.maxRetries; attempt++) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body,
        // 10-second timeout per attempt — hook scripts must not block Claude Code
        signal: AbortSignal.timeout(10000),
      });

      if (res.ok) {
        // Success — if this was a retry, log the recovery
        if (attempt > 0) {
          console.error(
            `[relay-forward] Delivery succeeded on attempt ${attempt + 1}`,
          );
        }
        return true;
      }

      // Determine retryability
      const status = res.status;

      if (status === 429) {
        // Rate limited — use Retry-After header if available
        const retryAfter = res.headers.get("Retry-After");
        const delayMs = retryAfter
          ? parseInt(retryAfter, 10) * 1000
          : computeDelay(attempt, retryConfig);
        console.error(
          `[relay-forward] Rate limited (429), retrying in ${delayMs}ms`,
        );
        lastError = new Error(`Rate limited (429)`);
        if (attempt < retryConfig.maxRetries) {
          await sleep(delayMs);
        }
        continue;
      }

      if (NON_RETRYABLE_STATUS.has(status)) {
        // Client error — do not retry
        console.error(
          `[relay-forward] Relay returned ${status} (client error, not retrying)`,
        );
        return false;
      }

      // 5xx or other — retry
      console.error(
        `[relay-forward] Relay returned ${status}, attempt ${attempt + 1}/${retryConfig.maxRetries + 1}`,
      );
      lastError = new Error(`HTTP ${status}`);

      if (attempt < retryConfig.maxRetries) {
        const delayMs = computeDelay(attempt, retryConfig);
        await sleep(delayMs);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);

      // Don't retry on AbortError from our own timeout (we'll retry on the next attempt)
      if (err instanceof DOMException && err.name === "TimeoutError") {
        console.error(
          `[relay-forward] Request timed out, attempt ${attempt + 1}/${retryConfig.maxRetries + 1}`,
        );
      } else {
        console.error(
          `[relay-forward] Failed to reach relay: ${message}, attempt ${attempt + 1}/${retryConfig.maxRetries + 1}`,
        );
      }

      lastError = err instanceof Error ? err : new Error(message);

      if (attempt < retryConfig.maxRetries) {
        const delayMs = computeDelay(attempt, retryConfig);
        await sleep(delayMs);
      }
    }
  }

  // Exhausted all retries
  console.error(
    `[relay-forward] Delivery failed after ${retryConfig.maxRetries + 1} attempts: ${lastError?.message ?? "unknown error"}`,
  );
  return false;
}

// =========================================================================
// Helpers
// =========================================================================

/**
 * Computes the delay for a retry attempt using exponential backoff with full jitter.
 *
 * Formula: min(cap, base * 2^attempt) with random jitter.
 * Full jitter: delay = random(0, computedDelay)
 *
 * This avoids thundering herd when multiple retries align.
 */
function computeDelay(attempt: number, config: RetryConfig): number {
  const exponential = config.baseDelayMs * Math.pow(2, attempt);
  const capped = Math.min(exponential, config.maxDelayMs);
  // Full jitter: random value in [0, capped]
  return Math.floor(Math.random() * capped);
}

/**
 * Promise-based sleep.
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
