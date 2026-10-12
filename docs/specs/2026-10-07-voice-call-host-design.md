# Voice Calls: ChatGPT's Voice Talks, Opus Works Behind It

Date: 2026-10-07. Status: approved in conversation ("Yes, go ahead and build it"). This replaces the
earlier draft in which Haiku hosted calls.

## Goal

Calls with a bot feel like talking to a person who knows Colin: instant answers to anything it already
knows, no double answers, and real work (lookups, actions, his Mac) done by Opus without stopping the
conversation. Long jobs, such as building a Walmart pickup cart, run while Colin keeps talking, and
what he says reaches the job as it happens.

## What Colin Asked For

- ChatGPT's realtime voice does most of the talking. Opus does not answer him directly on calls.
- A short ring (5 to 10 seconds is fine) while the voice is loaded with what it needs to know about him.
- If the voice can handle something, it does; if not, it hands off to Opus and lets Opus take over
  for that part. Opus can feed the voice information as it goes.
- While shopping (or any live job) the bot keeps working on his Mac while he keeps talking.
- Colin presses Place Order himself.

## Roles

| Part | Job |
|---|---|
| ChatGPT realtime voice (v3, via Codex on alien) | The only thing that speaks. Talks with Colin from a briefing, and hands off anything it cannot answer or do. |
| Relay (a small Codex agent on the voice's thread) | Turns each handoff into one of two hub tools: `ask_bot` (new question or job) or `update_job` (more detail for the job already running). Its final message is the handoff result the voice speaks from. |
| The bot (Opus via Hermes) | Does the work with its tools and memory, including Colin's Mac. Never speaks on the call itself. |

## Call Start: Ring and Briefing

- The app plays a ringback tone from the moment Colin calls until the voice greets him.
- The hub keeps a briefing per bot in `hub-state/briefings/<bot>.md`, rebuilt by Haiku at most every 10
  minutes after the bot's chat changes. It contains: the bot's personality (from its SOUL), what the
  bot knows about Colin (its Hermes memory files), the last 30 chat messages, open and waiting board
  tasks, and the date and time in Eastern time. The cap is 6,000 tokens.
- At call start the hub uses the briefing if it is under 30 minutes old; otherwise it rebuilds it while
  the phone rings. The briefing becomes part of the voice's instructions.
- Once audio connects and the voice is loaded, it greets Colin ("Hey Colin, how are you doing?").

## The Voice's Rules (in its instructions)

- Talk naturally and briefly, as the bot, using the briefing.
- Hand off when the answer needs anything not in the briefing, any lookup, any account (email,
  calendar, Trello, money), the web, his Mac, or doing anything. Say one short line first ("Let me
  check that"). Never invent facts.
- When a handoff result arrives, tell Colin in your own words, briefly, most useful part first.
- When text arrives to be spoken (progress from a running job, or a question from it), bring it up at
  a natural pause.

## Handoffs

- `clientManagedHandoffs` is off, so Codex runs the relay on each handoff and returns the relay's final
  message to the voice as the handoff result. The relay thread has no files, shell or network; its only
  tools are the two below.
- `ask_bot(request)`: the hub runs a bot turn with the prefix `[From a voice call]` and returns the
  reply. If the bot starts a long job (for example the Walmart cart), the turn keeps running as the
  call's **live job**, and `ask_bot` returns after 25 seconds with the bot's latest progress instead of
  waiting for the end.
- `update_job(note)`: sends the note to the live job as a Hermes steer (`/v1/runs/{run_id}/steer`), which
  the bot sees right after its current step. Returns "Passed on." immediately. If no job is running, it
  behaves like `ask_bot`.
- The relay chooses between the two: anything that adds to, changes or answers the running job is an
  update; anything else is a new request.

## While a Live Job Runs

- The bot's progress notes (Hermes commentary between tool calls) go to the voice with `appendSpeech`,
  at most one every 20 seconds and only when Colin is not mid-sentence. Questions from the bot always go
  through, at the next pause.
- When the job ends, its final reply goes to the voice the same way.
- Walmart: the bot builds the cart on Colin's Mac and stops at checkout with a read-back and the total.
  Colin chooses the pickup time and presses Place Order.

## After the Call

One wrap-up turn to the bot: a short summary of the call and anything to remember (for example grocery
likes and dislikes). Skipped when nothing new came up.

## Failure Handling

- Briefing build fails: start the call with the last briefing, or with only the personality and date.
- Relay or handoff fails: the voice says it could not get that and offers to try again.
- Bot busy with a chat turn: `ask_bot` waits up to 20 seconds, then the voice says the bot is finishing
  something.
- Steer rejected (job ended): the note becomes an `ask_bot`.

## Testing

- Recorded calls on alien (question audio padded with silence): small talk answered by the voice alone
  with no bot turn; a lookup handed off and answered once; a live job steered mid-run.
- Unit tests for the briefing builder and the job router.
