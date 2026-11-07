import { describe, expect, it, vi } from "vitest";

import { ApiError, TOKEN_KEY, apiGet, captureToken, isRealProvenance, parseTokenFromHash } from "./api";

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

describe("token handling", () => {
  it("parses #token=", () => {
    expect(parseTokenFromHash("#token=abc")).toBe("abc");
    expect(parseTokenFromHash("#other=1&token=x%2By")).toBe("x+y");
    expect(parseTokenFromHash("")).toBeNull();
    expect(parseTokenFromHash("#token=")).toBeNull();
  });

  it("moves the token from the fragment into storage and clears the fragment", () => {
    const storage = memoryStorage();
    const clear = vi.fn();
    expect(captureToken("#token=secret", storage, clear)).toBe("secret");
    expect(storage.data.get(TOKEN_KEY)).toBe("secret");
    expect(clear).toHaveBeenCalledOnce();
    expect(captureToken("", storage, clear)).toBe("secret"); // later navigation reuses the session token
    expect(clear).toHaveBeenCalledOnce();
  });

  it("returns null without any token", () => {
    expect(captureToken("", memoryStorage(), () => {})).toBeNull();
  });
});

describe("apiGet", () => {
  it("sends the bearer token and never credentials/cookies", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    await expect(apiGet("/api/health", "t0k", fetchImpl)).resolves.toEqual({ status: "ok" });
    const [path, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/health");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer t0k");
    expect(init.credentials).toBe("omit");
  });

  it("raises ApiError with the server detail", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ detail: "invalid or missing token" }), { status: 401 }));
    const error = await apiGet("/api/datasets", "bad", fetchImpl).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).message).toBe("invalid or missing token");
  });

  it("refuses non-API paths (the UI only talks to the local API)", async () => {
    await expect(apiGet("https://evil.example.com/x", "t", vi.fn())).rejects.toThrow("non-API");
  });
});

describe("provenance", () => {
  it("only real_historical and live_recorded are real", () => {
    expect(isRealProvenance("real_historical")).toBe(true);
    expect(isRealProvenance("live_recorded")).toBe(true);
    for (const p of ["synthetic", "sample", "unknown"]) expect(isRealProvenance(p)).toBe(false);
  });
});
