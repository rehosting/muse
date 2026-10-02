/** classifyUser mirrors the CLI: real prompts render, harness-injected wrappers
 * (system reminders, caveats, task-completion notifications) are hidden. */
import { describe, expect, it } from "vitest";
import { classifyUser } from "./ccInline";

describe("classifyUser", () => {
  it("keeps a plain typed prompt", () => {
    expect(classifyUser("fix the login bug")).toEqual({ kind: "text", text: "fix the login bug" });
  });

  it("hides a message that is only a task-notification", () => {
    const raw =
      "<task-notification>\n<task-id>ab1d69d144dff43c0</task-id>\n<status>completed</status>\n</task-notification>";
    expect(classifyUser(raw)).toEqual({ kind: "hidden" });
  });

  it("strips a task-notification but keeps surrounding user text", () => {
    const raw = "please continue\n<task-notification><task-id>x</task-id></task-notification>";
    expect(classifyUser(raw)).toEqual({ kind: "text", text: "please continue" });
  });

  it("still hides system reminders", () => {
    expect(classifyUser("<system-reminder>be nice</system-reminder>")).toEqual({ kind: "hidden" });
  });
});
