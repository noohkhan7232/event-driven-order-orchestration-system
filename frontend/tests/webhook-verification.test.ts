// @vitest-environment node
import { it, expect } from "vitest";
import { createHmac } from "node:crypto";
import { createRequire } from "node:module";
import { strings as s } from "@/lib/strings";
it("executes the copyable Node verifier against raw bytes, timestamp mismatch, tampering and replay", () => {
  // Execute our static sample itself rather than testing a separate implementation.
  const verify = new Function(
    "require",
    s.ops.verificationNode + "; return verifyOosWebhook;",
  )(createRequire(import.meta.url)) as (
    body: Buffer,
    headers: Record<string, string>,
    secret: string,
    now: number,
  ) => boolean;
  const secret = "SYNTHETIC_VERIFIER_SECRET",
    stamp = "1700000000",
    body = Buffer.from('{"name":"₹ tea"}');
  const digest = createHmac("sha256", secret)
    .update(Buffer.concat([Buffer.from(stamp + "."), body]))
    .digest("hex");
  const headers = {
    "X-OOS-Signature": "t=" + stamp + ",v1=" + digest,
    "X-OOS-Timestamp": stamp,
    "X-OOS-Delivery-Id": "fixture-delivery",
  };
  expect(verify(body, headers, secret, 1700000300)).toBe(true);
  expect(verify(body, headers, secret, 1700000301)).toBe(false);
  expect(verify(body, headers, secret, 1699999699)).toBe(false);
  expect(
    verify(Buffer.from('{ "name": "₹ tea" }'), headers, secret, 1700000000),
  ).toBe(false);
  expect(
    verify(
      body,
      { ...headers, "X-OOS-Timestamp": "1700000001" },
      secret,
      1700000000,
    ),
  ).toBe(false);
  expect(
    verify(
      body,
      {
        ...headers,
        "X-OOS-Signature": headers["X-OOS-Signature"] + ",v1=" + digest,
      },
      secret,
      1700000000,
    ),
  ).toBe(false);
});
