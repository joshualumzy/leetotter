import { expect, it } from "vitest";
import { remark } from "./remarks";

it("does not repeat a remark and only uses a person's name when it is set", () => {
  const now = new Date("2026-10-15T14:00:00");
  const anonymous = { name: null, minutesLeft: null, now };
  const first = remark("start", anonymous, () => 0);
  const second = remark("start", anonymous, () => 0);
  expect(second).not.toBe(first);
  for (let index = 0; index < 10; index++) expect(remark("start", anonymous, () => index / 10)).not.toContain("Ada");
  expect(remark("ended", { ...anonymous, name: "Ada" }, () => 0.99)).toContain("Ada");
});
