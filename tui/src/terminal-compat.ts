export type TerminalEnvironment = Record<string, string | undefined>

const TRUE_VALUES = new Set(["1", "true", "yes", "on"])
const FALSE_VALUES = new Set(["0", "false", "no", "off"])

/** SSH/Mosh sessions should leave mouse selection to the terminal emulator. */
export function isRemoteTerminal(
  environment: TerminalEnvironment = process.env,
): boolean {
  return Boolean(
    environment.SSH_TTY?.trim()
    || environment.SSH_CONNECTION?.trim()
    || environment.MOSH_CONNECTION?.trim(),
  )
}

/**
 * Decide whether OpenTUI should enable mouse reporting.
 *
 * Remote terminals default to native selection so clients such as Termius can
 * select and copy transcript text. PAWBOT_TUI_MOUSE=1 remains an opt-in for
 * remote terminals whose emulator supports OpenTUI mouse events.
 */
export function shouldUseTerminalMouse(
  environment: TerminalEnvironment = process.env,
): boolean {
  const preference = environment.PAWBOT_TUI_MOUSE?.trim().toLocaleLowerCase()
  if (preference && TRUE_VALUES.has(preference)) return true
  if (preference && FALSE_VALUES.has(preference)) return false
  return !isRemoteTerminal(environment)
}
