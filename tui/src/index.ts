import { PawbotTui, sessionExitMessage, type AppOptions } from "./app"

// Keep in sync with _TUI_DETACH_EXIT_CODE in pawbot/cli/tui_launcher.py.
const TUI_DETACH_EXIT_CODE = 90

function themePreference(): AppOptions["theme"] {
  const value = process.env.PAWBOT_TUI_THEME?.trim() || "auto"
  if (value === "auto" || value === "dark" || value === "light") return value
  throw new Error("PAWBOT_TUI_THEME must be auto, dark, or light")
}

const workspace = process.env.PAWBOT_TUI_WORKSPACE?.trim() || ""
const bootstrapUrl = process.env.PAWBOT_TUI_BOOTSTRAP_URL?.trim() || ""
const wsUrl = process.env.PAWBOT_TUI_WS_URL?.trim() || ""
const healthUrl = process.env.PAWBOT_TUI_HEALTH_URL?.trim() || ""
const gatewayStopCommand = process.env.PAWBOT_TUI_GATEWAY_STOP_COMMAND?.trim()
  || "pawbot gateway stop"
if (!bootstrapUrl && !wsUrl) {
  throw new Error("PAWBOT_TUI_BOOTSTRAP_URL or PAWBOT_TUI_WS_URL is required")
}
const options: AppOptions = {
  ...(bootstrapUrl
    ? {
        bootstrapUrl,
        bootstrapSecret: process.env.PAWBOT_TUI_BOOTSTRAP_SECRET?.trim() || "",
        healthUrl: healthUrl || undefined,
      }
    : { wsUrl }),
  apiUrl: process.env.PAWBOT_TUI_API_URL?.trim() || "",
  apiToken: process.env.PAWBOT_TUI_API_TOKEN?.trim() || "",
  chatId: process.env.PAWBOT_TUI_CHAT_ID?.trim() || undefined,
  model: process.env.PAWBOT_TUI_MODEL?.trim() || "unknown model",
  modelPreset: process.env.PAWBOT_TUI_MODEL_PRESET?.trim() || "default",
  workspace,
  version: process.env.PAWBOT_TUI_VERSION?.trim() || "dev",
  access: process.env.PAWBOT_TUI_ACCESS?.trim() || "workspace access",
  theme: themePreference(),
  onDetach: (chatId) => {
    process.exitCode = TUI_DETACH_EXIT_CODE
    process.stdout.write("Detached; the agent continues in the background.\n")
    if (chatId) process.stdout.write(sessionExitMessage(chatId))
    process.stdout.write(`Stop it with: ${gatewayStopCommand}\n`)
  },
  onExit: (chatId) => {
    process.stdout.write(sessionExitMessage(chatId))
  },
}

let app: PawbotTui | undefined
let shuttingDown = false

const shutdown = (code = 0) => {
  if (shuttingDown) return
  shuttingDown = true
  app?.stop()
  process.exitCode = code
}

for (const signal of ["SIGHUP", "SIGINT", "SIGTERM"] as const) {
  process.once(signal, () => shutdown())
}
process.once("exit", () => app?.stop())
process.once("uncaughtException", (error) => {
  shutdown(1)
  process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`)
})
process.once("unhandledRejection", (error) => {
  shutdown(1)
  process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`)
})

app = await PawbotTui.create(options)
if (shuttingDown) app.stop()
else await app.start()
