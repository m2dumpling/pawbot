import {
  BoxRenderable,
  RGBA,
  StyledText,
  TextAttributes,
  TextRenderable,
  type CliRenderer,
  type KeyEvent,
  type TextChunk,
} from "@opentui/core"

import type { ToolApprovalRequest } from "./protocol"

export interface ApprovalNoticeTheme {
  text: string
  muted: string
  border: string
  accent: string
  warning: string
  error: string
}

interface ApprovalNoticeOptions {
  onDecision: (decision: "approved" | "denied") => void
}

/** A keyboard- and mouse-friendly confirmation surface for a pending Tool. */
export class ApprovalNotice {
  readonly root: BoxRenderable
  private readonly title: TextRenderable
  private readonly detail: TextRenderable
  private readonly approve: TextRenderable
  private readonly deny: TextRenderable
  private readonly onDecision: (decision: "approved" | "denied") => void
  private request: ToolApprovalRequest | null = null
  private busy = false

  constructor(
    renderer: CliRenderer,
    private theme: ApprovalNoticeTheme,
    options: ApprovalNoticeOptions,
  ) {
    this.onDecision = options.onDecision
    this.root = new BoxRenderable(renderer, {
      id: "pawbot-tui-approval-notice",
      width: "100%",
      height: 5,
      flexShrink: 0,
      flexDirection: "column",
      border: true,
      borderStyle: "rounded",
      borderColor: theme.border,
      paddingLeft: 1,
      paddingRight: 1,
      visible: false,
      backgroundColor: RGBA.defaultBackground(),
    })
    const header = new BoxRenderable(renderer, {
      id: "pawbot-tui-approval-header",
      width: "100%",
      height: 1,
      flexDirection: "row",
      alignItems: "center",
      gap: 2,
    })
    this.title = new TextRenderable(renderer, {
      id: "pawbot-tui-approval-title",
      width: "auto",
      minWidth: 0,
      flexGrow: 1,
      height: 1,
      truncate: true,
      selectable: false,
    })
    this.deny = this.action(renderer, "deny", "[d] Deny", "denied")
    this.approve = this.action(renderer, "approve", "[a] Approve", "approved", true)
    this.detail = new TextRenderable(renderer, {
      id: "pawbot-tui-approval-detail",
      width: "100%",
      height: 2,
      truncate: true,
      selectable: false,
    })
    header.add(this.title)
    header.add(this.deny)
    header.add(this.approve)
    this.root.add(header)
    this.root.add(this.detail)
  }

  get visible(): boolean {
    return this.root.visible
  }

  show(request: ToolApprovalRequest): void {
    this.request = request
    this.busy = false
    this.root.visible = true
    this.render()
  }

  hide(): void {
    this.request = null
    this.busy = false
    this.root.visible = false
  }

  setBusy(busy: boolean): void {
    this.busy = busy
    if (this.visible) this.render()
  }

  setTheme(theme: ApprovalNoticeTheme): void {
    this.theme = theme
    this.root.borderColor = theme.border
    if (this.visible) this.render()
  }

  handleKey(key: KeyEvent): boolean {
    if (!this.visible) return false
    if (!this.busy && (key.name === "a" || key.name === "y")) {
      this.approveDecision("approved")
    } else if (!this.busy && (key.name === "d" || key.name === "n" || key.name === "escape")) {
      this.approveDecision("denied")
    }
    key.preventDefault()
    return true
  }

  private approveDecision(decision: "approved" | "denied"): void {
    if (!this.request || this.busy) return
    this.busy = true
    this.render()
    // The callback owns the async request and calls setBusy(false) on failure.
    this.onDecision(decision)
  }

  private action(
    renderer: CliRenderer,
    id: string,
    label: string,
    decision: "approved" | "denied",
    primary = false,
  ): TextRenderable {
    return new TextRenderable(renderer, {
      id: `pawbot-tui-approval-${id}`,
      content: label,
      width: label.length,
      height: 1,
      flexShrink: 0,
      selectable: false,
      onMouseOver: () => {
        if (!this.busy) {
          const target = primary ? this.approve : this.deny
          target.attributes = TextAttributes.BOLD | TextAttributes.UNDERLINE
        }
      },
      onMouseOut: () => this.render(),
      onMouseDown: (event) => {
        if (event.button !== 0 || this.busy) return
        event.preventDefault()
        event.stopPropagation()
        renderer.clearSelection()
        this.approveDecision(decision)
      },
    })
  }

  private render(): void {
    if (!this.request) return
    const parameters = JSON.stringify(this.request.arguments)
    const preview = parameters.length > 120 ? `${parameters.slice(0, 120)}…` : parameters
    this.title.content = new StyledText([
      chunk("⚠ ", this.theme.warning),
      chunk(`Approval needed · ${this.request.name}`, this.theme.text, true),
    ])
    this.detail.content = new StyledText([
      chunk(`  ${preview}\n  ${this.busy ? "Submitting decision…" : "Approve or deny before the Tool runs."}`, this.theme.muted),
    ])
    this.deny.fg = RGBA.fromHex(this.busy ? this.theme.muted : this.theme.text)
    this.approve.fg = RGBA.fromHex(this.busy ? this.theme.muted : this.theme.accent)
    this.deny.attributes = 0
    this.approve.attributes = TextAttributes.BOLD
  }
}

function chunk(text: string, color: string, bold = false): TextChunk {
  return {
    __isChunk: true,
    text,
    fg: RGBA.fromHex(color),
    attributes: bold ? TextAttributes.BOLD : 0,
  }
}
