import pino from "pino";

const level =
  process.env.NEXT_PUBLIC_LOG_LEVEL ??
  (process.env.NODE_ENV === "production" ? "info" : "debug");

const isBrowser = typeof window !== "undefined";

export const logger = pino({
  level,
  base: { service: "web" },
  ...(isBrowser
    ? {
        browser: {
          asObject: true,
        },
      }
    : {}),
});

export function logError(
  err: unknown,
  message: string,
  context?: Record<string, unknown>,
): void {
  if (err instanceof Error) {
    logger.error({ err, ...context }, message);
    return;
  }
  logger.error({ err: String(err), ...context }, message);
}
