# Connect Google Design

Date: 2026-10-08. Status: built on branch `google-connect-v1` for Colin's review. Not deployed. Answers QA round 1,
finding H5 ("a new person cannot connect Google"), with the route the QA report recommended: reuse the connectors
on the person's own Claude account through the official Claude Code on their Mac.

## How Does a Person Connect Google Today?

Checked on the web and on this Mac on 2026-10-08 (sources at the end).

1. **On claude.ai.** Customize, then Connectors (`https://claude.ai/customize/connectors`). The plus button next to
   Connectors opens the directory. Pick Gmail, tap Connect, sign in on Google's page and allow access. Google
   Calendar and Google Drive are separate connectors, each connected the same way. All plans have them, including
   Free. On Team and Enterprise plans an owner must enable them for the organization first. Installing connectors
   from the Claude iPhone app is in beta, so the web is the main path.
2. **In Claude Code.** When Claude Code is signed in with a claude.ai plan login, it fetches that account's
   connectors by itself, named `claude.ai Gmail`, `claude.ai Google Calendar`, `claude.ai Google Drive` and
   `claude.ai Trello`. This works in terminal, Agent SDK and `claude -p` sessions, which is what the `connectors`
   tool already uses on alien. Connectors do not load when Claude Code uses an API key, `apiKeyHelper`, a
   `setup-token` token, or Bedrock, Vertex or Foundry.
3. **Gmail and Calendar can only be connected on claude.ai.** Claude Code refuses to run its own sign-in for them
   ("is Anthropic-hosted and doesn't support local OAuth"). So Zero cannot connect Google for the person; it can
   only send them to claude.ai and watch for the result.

## What Can Zero Detect Without Reading a Token?

Two read-only Claude Code commands. Neither calls the model, so a check costs nothing against the plan's limits.

| Command | What Zero keeps | Time on this Mac |
|---|---|---|
| `claude auth status --json` | `loggedIn`, the login kind (`claude.ai` or not) and the account email | 0.14 s |
| `claude mcp get "claude.ai Gmail"` (one per service, side by side) | the `Status:` line only | about 1.3 s each, 2.2 s for all four |

States the hub reports per service: connected, not connected (not on the account), needs sign-in (expired or
unfinished), turned off (switched off in Claude Code's `/mcp` for the home folder), trouble (failed to connect),
and four Claude-side states (signed out, using an API key, session rejected, Claude Code missing). Server
addresses, org ids and everything else in the output are dropped; a test checks this.

Verified live on this Mac: all four of Colin's connectors read as connected through the new module.

## What Was Built

**Hub** (`hub/taffy_hub/accounts.py`, routes in `app.py`)
- `GET /api/accounts` (cached up to ten minutes) and `POST /api/accounts/check` (fresh; calls within three
  seconds share one answer, and concurrent calls run one check). Commands run with the same home, config and
  folder as the Zips' `claude -p` runs, so the check sees the same login and switches.
- Every 30 minutes the hub checks again. An account that worked before and now needs the person (sign in again,
  Claude signed out) gets one notification; accounts never connected are not news.
- Welcome's "Your Accounts" now lists what really works instead of a fixed list.
- Tests: `hub/tests/test_accounts.py`, 20 tests (parsing, no secrets in output, cache, cards, waking the Zip,
  alerts, and the plugin's connectors tool). The full suite has the same 5 failures as before this branch
  (environment-specific tests in test_team, test_watcher and test_consent).

**Zips** (`alien/plugins/taffy-tools/tools.py`)
- Before a run, the `connectors` tool asks the hub. If the account is not connected, no `claude -p` runs: a
  "Connect Google" card goes up in that Zip's chat with a notification, and the Zip is told to stop and say what
  it will do once connected. One Google card covers all three Google services; Trello has its own.
- If a run's reply shows the account was missing anyway (it expired between checks), the hub checks again and
  raises the card.
- When a check sees the account connected, the card closes and the Zip gets a hub note to carry on, like the
  sign-in card. A status Zero cannot read never blocks a Zip.

**App**
- Connect Google (`ConnectAccountsView.swift`): what Zips can do with each account (matching the tool's deny list:
  no sending, deleting, sharing, label changes or answering invitations), Open Claude Settings, Check Again, live
  rows that poll every 5 seconds while the screen is open, numbered steps naming the Claude account Zero uses,
  disconnect guidance with what happens to the Zips, and a privacy line. Clear notes for Claude signed out or on
  an API key. The same view does Connect Trello.
- Settings: a Connected Accounts section (Google, Trello) with status lines.
- Onboarding: a Connect Google step after Why We Ask and before Pick My Voice, with Show Me How and Skip for Now.
- Chat: the Zip's Connect card, which opens the flow in a sheet.
- The guided browser overlay from `voice-setup-v1` is not merged into this base, so it was not reused here. The
  screen copies its layout and its "card of steps" fallback. Once that branch lands, a claude.ai guide script
  for the same overlay is a small addition.

Builds: iOS Simulator and Mac Catalyst both succeed. Screens (fixture mode, iPhone 17 Pro simulator) are in
`ios/build/screens/` and copied, smaller, to `docs/specs/google-connect/`. Mac screens were not captured: Colin was
using this Mac's display, and the off-screen QA window could not be captured without bringing it to the front.

DEBUG launch arguments: `-fixture -open connect [trello]`, `-accounts none|partial|all|expired|signedout`,
`-scrollto steps|disconnect`, `-onboarding google [-guide]`, `-open money` (the card). In fixture mode, Open Claude
Settings simulates the rows turning green one at a time.

## What I Could Not Confirm

1. **A brand-new account end to end.** No account was signed in or changed. The exact `Status:` text for "session
   token rejected" and for a connector added on claude.ai but never authorized is inferred from the docs; both map
   to a state the person can act on, and unknown text never blocks a Zip.
2. **How fast a new connection shows up.** `claude mcp get` appears to fetch the list from claude.ai each run, but
   I could not add a connector to test the delay. If Claude Code caches the list, the row may take a minute.
3. **Gmail sending.** The support page now says the Gmail connector can send, reply and forward, asking for
   approval by default. Claude Code on Colin's account shows no send tool today. If one appears, it must join the
   `connectors` tool's deny list (exact tool name unknown), or `claude -p` may treat "ask" as a refusal.
4. **Whether the person needs to be on claude.ai or the Claude app.** On an iPhone with the Claude app installed,
   the link may open the app, where connector setup is beta.

## Open Questions for Colin

1. **Terms.** Anthropic allows a person to sign in to the unmodified Claude Code binary with their own plan, and
   says plan limits "assume ordinary, individual usage of Claude Code and the Agent SDK". Is a Mac app that runs
   Claude Code in the background for Zips ordinary use? Worth asking Anthropic before release.
2. **Usage.** Each connector call is a short Haiku `claude -p` run on the person's plan. Since October 7, Max and
   Team plans include monthly API credits that cover `claude -p`; Pro draws on plan limits. Fine for now?
3. **Onboarding order.** Connect Google sits after Why We Ask. Should it come after Pick My Voice instead, or be
   offered only when a suggested Zip needs Google?
4. **ChatGPT-only people.** Nothing here helps someone without a Claude plan. Keep "Connect Google" hidden for them
   until the Codex path is tested (QA H5, path 2)?
5. **Trello and others.** The flow handles Trello. Should Zero list other connectors a person already has (Notion,
   Slack) so Zips can use them?

## Sources

- Claude Code, "Use MCP servers from claude.ai", `https://code.claude.com/docs/en/mcp` (connectors in Claude Code,
  login requirement, statuses, Anthropic-hosted Gmail and Calendar).
- Claude Code errors, `https://code.claude.com/docs/en/errors` (session token rejected; no local OAuth).
- Claude Code legal and compliance, `https://code.claude.com/docs/en/legal-and-compliance`.
- "Use Google Workspace connectors", `https://support.claude.com/en/articles/10166901`.
- "Use connectors to extend Claude's capabilities", `https://support.claude.com/en/articles/11176164`.
- Agent SDK and `claude -p` usage, `https://support.claude.com/en/articles/15036540` (updates of June 15 and
  October 7, 2026).
- Local: `claude --version` 2.1.292 on this Mac, 2.1.294 on alien; `claude mcp list`, `claude mcp get` and
  `claude auth status --json` output inspected with addresses and ids removed.
