import { it, expect } from "vitest";
import { calendarDate } from "@/lib/format";
it("formats date-only delivery estimates without shifting their day across timezones", () => {
  const previous = process.env.TZ;
  try {
    process.env.TZ = "America/Los_Angeles";
    expect(calendarDate("2026-11-25", "en-IN")).toEqual({
      label: "25 Nov 2026",
      tooltip: "Wednesday, 25 November 2026 IST",
    });
    process.env.TZ = "Asia/Kolkata";
    expect(calendarDate("2026-11-25", "en-IN").label).toBe("25 Nov 2026");
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});
