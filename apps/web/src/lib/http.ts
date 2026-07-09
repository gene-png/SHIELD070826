/**
 * True when an error is an AbortController/AbortSignal cancellation (a
 * user-initiated cancel or a timeout), so callers can show a calm "canceled"
 * message instead of an alarming failure. Works for DOMException and any
 * Error whose name is "AbortError".
 */
export function isAbortError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "name" in err &&
    (err as { name?: unknown }).name === "AbortError"
  );
}
