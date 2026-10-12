# Bots Using Colin's Mac

Date: 2026-10-07. Status: design approved in conversation ("go ahead and build that").

## Goal

When Colin explicitly asks a bot to do something on his Mac, the bot can do it: open pages, files and apps, run Terminal commands, see the screen, and click, type, press keys and scroll. Colin approves once per task. Nothing happens on the Mac outside an approved task.

## Decisions Colin Made

- Scope: both opening and running things, and seeing, clicking and typing.
- Approval: once per task. After Allow, the bot acts freely until the task ends, 30 minutes pass, or Colin presses Stop.

## Parts

### 1. The Mac helper (inside Taffy.app)

The Mac app is a Mac Catalyst app, which cannot run processes, capture the screen or post input events. A small native macOS bundle, `TaffyMacBridge.bundle`, ships inside Taffy.app (`Contents/PlugIns`) and is loaded only on the Mac. It provides:

| Action | How |
|---|---|
| open a URL, file, or app | `NSWorkspace` |
| run a command | `/bin/zsh -lc` in the home folder; 120 s limit; stdout and stderr capped at 20,000 characters each |
| screenshot | ScreenCaptureKit, main display, scaled to at most 1,440 px wide, JPEG; returns the scale so clicks map back |
| UI list | Accessibility tree of the frontmost app: up to 250 elements with role, title or value, and on-screen center |
| click, double click, right click, drag | `CGEvent` at screen points |
| type text | `CGEvent` keyboard strings; refused when the focused element is a secure (password) field |
| key combo | `CGEvent` with modifiers ("cmd+l", "return") |
| scroll | `CGEvent` scroll wheel |
| approval card | floating `NSPanel` on top of every window and Space: the bot, its task, Colin's own words, Deny and Allow |
| in-use bar | floating `NSPanel` while a task is active: "Taffy is using your Mac" with Stop |

Permissions: Accessibility (click, type, UI list) and Screen Recording (screenshots), requested the first time they are needed. Open and run need neither.

### 2. The link to the hub

While the Mac app is open it holds one WebSocket to the hub (`/api/mac/link`, device token). The hub sends `{id, op, args}`; the Mac answers `{id, ok, result | error}`. The app reconnects with backoff, so bots see the Mac as online whenever Taffy is open.

### 3. The hub (`hub/taffy_hub/mac.py`)

- Keeps the current link and the active grant, if any: bot, task, Colin's words, start, expiry.
- `mac_start`: refuses if the Mac is offline or another bot holds a grant; otherwise shows the approval card and waits up to 120 s.
- Every other action requires an active grant for the calling bot. The grant ends when Colin presses Stop, after 30 minutes, when the bot calls `mac_done`, or when that bot's turn finishes (`turns.py` reports turn ends).
- Command guard (refused even inside a grant): `sudo`, `su`, `diskutil erase*`, `rm` with `-r` on `/`, `~` or paths outside the home folder, `security` keychain reads, `launchctl` changes, anything reading `~/.ssh`, `~/Library/Keychains`, browser cookie or login stores, or Taffy's own secrets. This is a tripwire, not a boundary; the grant and Colin's approval are the real control.
- Called by the bots' tools over HTTP on 127.0.0.1 with a key in `hub-state/internal-key` (mode 600).

### 4. The bots' tools (taffy-tools plugin)

`mac_start(task, colin_said)`, `mac_open(target)`, `mac_run(command)`, `mac_screenshot()`, `mac_ui()`, `mac_click(x, y, button?, count?)`, `mac_type(text)`, `mac_key(keys)`, `mac_scroll(x, y, dy)`, `mac_done()`.

The tool description and every bot's rules say: use the Mac only when Colin explicitly asked in this conversation; quote his words in `colin_said`; never type passwords (sign-ins use the saved-logins flow); anything that sends, buys or deletes still needs his confirmation in chat. Screenshots come back to the model as images.

## Error Handling

- Mac offline or app closed: "Colin's Mac isn't connected. Taffy needs to be open on it."
- Denied or no answer in 120 s: the bot is told and must not retry in the same task without asking Colin.
- Missing permission: the Mac shows the macOS prompt; the bot is told which permission is missing.
- Link drops mid-task: the grant ends.

## Testing

- Hub unit tests: the grant lifecycle (start, expiry, Stop, turn end, other bot refused) and the command guard.
- Live run with Colin watching: a bot opens a page, runs `ls ~/Downloads`, takes a screenshot, reads the UI list and clicks a button; then Stop, and the next action is refused.
