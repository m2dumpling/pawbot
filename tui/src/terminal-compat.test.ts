import { describe, expect, test } from "bun:test"

import { isRemoteTerminal, shouldUseTerminalMouse } from "./terminal-compat"

describe("terminal compatibility", () => {
  test("detects SSH and Mosh sessions", () => {
    expect(isRemoteTerminal({ SSH_TTY: "/dev/pts/1" })).toBe(true)
    expect(isRemoteTerminal({ SSH_CONNECTION: "198.51.100.2 22" })).toBe(true)
    expect(isRemoteTerminal({ MOSH_CONNECTION: "1.2.3.4:60000" })).toBe(true)
    expect(isRemoteTerminal({ TERM: "xterm-256color" })).toBe(false)
  })

  test("prefers native terminal selection over remote mouse reporting", () => {
    expect(shouldUseTerminalMouse({ SSH_TTY: "/dev/pts/1" })).toBe(false)
    expect(shouldUseTerminalMouse({ SSH_TTY: "/dev/pts/1", PAWBOT_TUI_MOUSE: "1" })).toBe(true)
    expect(shouldUseTerminalMouse({ PAWBOT_TUI_MOUSE: "0" })).toBe(false)
    expect(shouldUseTerminalMouse({ TERM: "xterm-256color" })).toBe(true)
  })
})
