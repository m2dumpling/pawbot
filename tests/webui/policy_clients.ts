/** Real client implementations, sockets and mutations; only the model API is scripted. */
import assert from "node:assert/strict";
import { join } from "node:path";
import { PawbotClient as WebClient } from "../../webui/src/lib/pawbot-client";
import { PawbotClient as TuiClient } from "../../tui/src/protocol";

const args = await Bun.file(process.argv[2]).json();
const events: any[] = [];
const waiters = new Set<() => void>();
function receive(event: any) {
  events.push(event);
  for (const wake of [...waiters]) wake();
}
function waitFor(predicate: (event: any) => boolean, start = 0): Promise<any> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      waiters.delete(check);
      reject(new Error(`Timed out; recent events: ${JSON.stringify(events.slice(-12))}`));
    }, 25_000);
    const check = () => {
      const found = events.slice(start).find(predicate);
      if (found) { clearTimeout(timer); waiters.delete(check); resolve(found); }
    };
    waiters.add(check);
    check();
  });
}
const url = new URL(args.bootstrap.ws_url);
url.searchParams.set("token", args.bootstrap.token);
url.searchParams.set("client_id", `policy-${args.client}`);
const scope = { project_path: args.workspace, access_mode: "restricted" as const };
const tui = args.client === "tui";
const web = tui ? null : new WebClient({ url: url.toString(), reconnect: false });
const terminal = tui ? new TuiClient({
  url: url.toString(), initialWorkspaceScope: scope, onEvent: receive, onStatus() {},
}) : null;
const unsubscribers: Array<() => void> = [];
if (web) web.onStatus(status => receive({ event: "status", status }));
const connect = terminal || web!;
async function newChat() {
  const start = events.length;
  if (terminal) {
    terminal.newChat(scope);
    return (await waitFor(e => e.event === "attached", start)).chat_id as string;
  }
  const chat = await web!.newChat(10_000, scope);
  unsubscribers.push(web!.onChat(chat, receive));
  return chat;
}
function send(chat: string, content: string) {
  if (terminal) return terminal.send(content);
  const turn = crypto.randomUUID();
  web!.sendMessage(chat, content, undefined, { workspaceScope: scope, turnId: turn });
  return turn;
}
async function resolve(request: string, chat: string, decision: "approved" | "denied") {
  if (terminal) return terminal.resolveToolApproval(request, chat, decision);
  const result = await web!.requestMutation<any>("tool.approval.resolve", {
    request_id: request, chat_id: chat, decision,
  });
  return result.resolved === true;
}
const completed: string[] = [];
try {
  connect.connect();
  if (terminal) await waitFor(e => e.event === "attached");
  else await waitFor(e => e.event === "status" && e.status === "open");
  for (const mode of ["inline", "background"]) {
    for (const action of ["approve", "deny", "stop"]) {
      const name = `${action}_${mode}`;
      const chat = await newChat();
      const start = events.length;
      const turn = send(chat, `PAWBOT_POLICY:${name}`);
      const spawn = await waitFor(e => e.chat_id === chat && e.event === "tool_approval"
        && e.request.name === "spawn", start);
      assert.equal(await resolve(spawn.request.request_id, chat, "approved"), true);
      const write = await waitFor(e => e.chat_id === chat && e.event === "tool_approval"
        && e.request.name === "write_file", start);
      assert.equal(await Bun.file(join(args.workspace, name + ".txt")).exists(), false);
      await assert.rejects(resolve(write.request.request_id, "wrong-chat", "approved"));
      if (action === "stop") {
        send(chat, "/stop");
        await waitFor(e => e.chat_id === chat && e.event === "message"
          && /Stopped \d+ task/.test(e.text), start);
        await assert.rejects(resolve(write.request.request_id, chat, "approved"));
      } else {
        assert.equal(await resolve(write.request.request_id, chat,
          action === "approve" ? "approved" : "denied"), true);
        await waitFor(e => e.chat_id === chat && e.event === "turn_end" && e.turn_id === turn, start);
      }
      const output = Bun.file(join(args.workspace, name + ".txt"));
      // A background child can outlive the parent's bounded terminal wait.
      if (action === "approve") {
        const deadline = Date.now() + 5_000;
        while (!(await output.exists()) && Date.now() < deadline) await Bun.sleep(20);
      }
      assert.equal(await output.exists(), action === "approve");
      if (action === "approve") assert.equal(await output.text(), name);
      completed.push(name);
    }
  }
  for (const action of ["continue", "dismiss"] as const) {
    const chat = `recovery-${action}`;
    const start = events.length;
    if (web) unsubscribers.push(web.onChat(chat, receive));
    else terminal!.attach(chat);
    const attached = await waitFor(e => e.event === "attached" && e.chat_id === chat, start);
    const state = attached.recovery_state;
    assert.equal(state.status, "awaiting_user");
    assert.equal(state.reason, "tool_state_unknown");
    if (terminal) await terminal.updateRecovery(action, chat, state.recovery_id);
    else await web!.requestMutation(`recovery.${action}`, { chat_id: chat, recovery_id: state.recovery_id });
    await waitFor(e => e.chat_id === chat && e.event === "recovery_state"
      && (e.status === "recovered" || (action === "continue" && e.status === "resuming")), start);
    if (action === "continue") await waitFor(e => e.chat_id === chat && e.event === "turn_end", start);
    completed.push(`recovery_${action}`);
  }
  console.log(JSON.stringify({ client: args.client, completed }));
} finally {
  for (const unsubscribe of unsubscribers) unsubscribe();
  connect.close();
}
