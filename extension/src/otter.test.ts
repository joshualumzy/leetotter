import { expect, it } from "vitest";
import { touchForDate } from "./otter";

const local = (year: number, month: number, day: number, hour: number, minute = 0) =>
  new Date(year, month - 1, day, hour, minute);

it("selects each time touch at its local hour boundaries", () => {
  for (const [hour, expected] of [
    [0, "night"], [4, "night"], [5, "morning"], [10, "morning"],
    [11, ""], [12, "afternoon"], [17, "afternoon"], [18, ""], [22, ""], [23, "night"],
  ] as const) expect(touchForDate(local(2026, 10, 15, hour))).toBe(expected);
  expect(touchForDate(local(2026, 10, 15, 10, 59))).toBe("morning");
  expect(touchForDate(local(2026, 10, 15, 17, 59))).toBe("afternoon");
});

it("adds a weekend flower to a time touch or by itself", () => {
  expect(touchForDate(local(2026, 10, 17, 6))).toBe("morning weekend");
  expect(touchForDate(local(2026, 10, 18, 14))).toBe("afternoon weekend");
  expect(touchForDate(local(2026, 10, 17, 23))).toBe("night weekend");
  expect(touchForDate(local(2026, 10, 18, 11))).toBe("weekend");
});

it("uses festivals in place of every other touch, including their edges", () => {
  for (const [year, month, day] of [[2026, 2, 17], [2027, 2, 6], [2028, 1, 26], [2029, 2, 13], [2030, 2, 3]]) {
    expect(touchForDate(local(year, month, day - 1, 23, 59))).not.toBe("lny");
    expect(touchForDate(local(year, month, day, 0))).toBe("lny");
    expect(touchForDate(local(year, month, day + 2, 23, 59))).toBe("lny");
    expect(touchForDate(local(year, month, day + 3, 0))).not.toBe("lny");
  }
  for (const [month, first, last, touch] of [[10, 25, 31, "halloween"], [12, 20, 26, "xmas"]] as const) {
    expect(touchForDate(local(2026, month, first - 1, 23, 59))).not.toBe(touch);
    expect(touchForDate(local(2026, month, first, 0))).toBe(touch);
    expect(touchForDate(local(2026, month, last, 23, 59))).toBe(touch);
    expect(touchForDate(local(2026, month, last + 1, 0))).not.toBe(touch);
  }
});
