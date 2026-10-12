# Bots Talking to Each Other

Date: 2026-10-07. Status: built and live on alien at 7:00 PM ET (see As Built at the end).

## Why

Colin asked for bots to be able to message each other. Today they can only hand work through the kanban board: Taffy creates a task for a specialist, the specialist finishes, blocks or asks for review, and a board note comes back to Taffy. That works for real jobs. It fails for everything smaller, and the gaps already show:

- On the 6:01 PM call, Taffy needed Acme Homes details that live with Career. The only options were a full board task (too slow for a call) or guessing from Taffy's own stale memory, which said Colin had not applied yet.
- School may notice something Money should know (a tuition charge), but nothing carries that across except Colin.
- Each bot's memory is its own. When Colin tells one bot a fact about his life, the others never learn it.

## Principles

1. **Colin stays in charge.** Bots talk to get work done, never to decide things for him. One bot can never give another the go-ahead for anything that needs Colin's yes (sending, buying, submitting, signing in, using his Mac).
2. **Everything is visible.** Every message between bots shows in the app, and Colin can step into any of them.
3. **The lightest channel that works.** A quick question should not become a task, and a real job should not hide inside a chat.
4. **Bounded.** Hard limits stop loops and runaway cost.
5. **Words from another bot are information, not orders.** A bot weighs them like anything else it reads; only Colin's own words carry his authority.

## The Five Ways Bots Interact

| Kind | Example | Channel | Waits for an answer? |
|---|---|---|---|
| Hand-off | Taffy gives School "find the POL 101 rubric" | Board task (exists today) | No; the result comes back as a board note |
| Quick question | Taffy asks Career "did Colin apply to Acme Homes, and who is the contact?" | New `ask_bot` | Yes, within about 30 seconds |
| Heads-up | School tells Money "a $412 tuition charge posted today" | New `tell_bot` | No; it rides along with Money's next turn |
| Team fact | Any bot records "Colin applied to Acme Homes on Oct 7" | New shared team notes | No; every bot and the call voice see it |
| Joint job | "Plan my week": School, Money and Career each do a part | Parent task with linked child tasks | No; Taffy pulls the results together |

### 1. Hand-off (keep, and open it up)

Board tasks stay the way real work moves. Colin decided specialists talk to each other directly: any bot can hand a piece of work to any other bot, not only through Taffy. A hand-off made inside a task is a child task linked to it, so the board shows who asked whom and why.

### 2. Quick question: `ask_bot(bot, question)`

- The asking bot gets a short answer back inside its own turn, the way a person would ping a coworker.
- The hub runs the question as a short turn on the other bot, in that bot's **team session**, a separate conversation from Colin's chat with it. That keeps Colin's chats clean and lets the answer come even while the bot is mid-chat with him, if Hermes allows two sessions per bot at once. If it does not, the question waits up to 30 seconds and then returns "Career is busy right now."
- The answering bot can look things up (memory, its files, read-only tools) but takes no actions for the asker and asks no questions of its own. This is what keeps chains from forming.
- Answers carry where they came from ("from my notes", "checked the email just now") so the asker can judge freshness.
- During calls this is how the voice gets specialist knowledge: the voice hands off to Taffy, Taffy asks Career, and the answer comes back while Colin is still talking.

### 3. Heads-up: `tell_bot(bot, note)`

- One way only; it never starts a turn, costs nothing until the other bot is next working, and cannot loop.
- The note is added as quiet context to the other bot's next turn ("From School: a $412 tuition charge posted today.") and shows in the Team view.
- For anything Colin should hear about, the sender tells Taffy, and Taffy decides whether it is worth his attention.

### 4. One shared picture of Colin, plus each bot's specialty

Colin's decision: every bot sees the main facts about him, and those stay consistent across the team, but not all data is shared. Each bot knows what its specialty is and where that area's detail lives.

- **Core profile (shared by all):** who Colin is, the people in his life, his routines and preferences, his current priorities and big open items (for example "applied to Acme Homes on Oct 7"). Kept as team notes, below.
- **Specialty detail (stays with the owner):** Money keeps balances, transactions and bills; School keeps grades, assignments and course detail; Career keeps the Acme Homes specifics; and so on. Other bots know which bot owns which area and ask it (`ask_bot`) when they need a specific detail, getting only what the question needs.
- Each bot's rules list the team's specialties ("Money owns finances; ask Money for balances"), so no bot guesses at another's area from stale notes.

### Team notes (the shared core)

- One shared file of short, dated, attributed facts about Colin's life and the team's work: `taffy/shared/team-notes.md`, written through a `team_note(fact)` tool, never edited by hand by a bot.
- Every bot reads the latest notes at the start of a turn, and the call voice's briefing includes them. Newest wins when two notes disagree; the older one is marked replaced, not deleted.
- **Need-to-know stays private.** Money's account numbers and balances, passwords, health, and anything Colin marked private never go in team notes. A bot keeps those in its own memory and answers specific questions about them through `ask_bot` with only what the question needs.
- Every bot's memory instructions change from "save to your memory" to "save facts about Colin's life to team notes; save how you do your own job to your memory."

### 5. Joint job

Taffy breaks the request into child tasks under one parent and links dependencies (Money's part waits on School's), using the board's existing links. Each specialist sees the parent's goal. When the last child finishes, Taffy gets one board note with all of it and gives Colin one answer.

## What Colin Sees

Colin's decision: there is no separate Team view. Bot-to-bot messages appear inside the chats he already has with each bot, styled like Grok Bot's "Sent via" messages:

- **The message:** a lightly tinted box in the sending bot's color (not a gray bot bubble and not Colin's black one), with Show more when it is long.
- **The caption above it:** the sender's small avatar and a line such as "Taffy messaged Money", "Taffy asked Career", "School gave Money a heads-up" or "Money handed Builder a task".
- **Where it appears:** in the chat of the bot it was sent to. In Money's chat, Colin sees Taffy's tinted message, then Money's normal reply under it. In the sender's chat, the answer it got back appears the same way, tinted in the replying bot's color, with "Money replied to Taffy" above.
- **Team notes:** a new note shows once, small and gray, in the chat of the bot that wrote it ("Saved to team notes: you applied to Acme Homes on Oct 7").
- **Stepping in:** Colin replies in that chat as usual. If a bot-to-bot exchange is still running there, his reply reaches it as a mid-task update.
- Nothing between bots sends Colin a notification. Only results meant for him do, as today.

## Limits

- **Depth 1.** A bot answering a question cannot ask another bot anything inside that answer. Heads-ups never start turns. Hand-offs can only be one level below the task that made them.
- **Per job:** at most 3 questions to other bots, and at most 2 child hand-offs from a specialist.
- **Repeats:** the same question to the same bot within an hour returns the earlier answer.
- **Daily cap:** 100 bot-to-bot questions across the team; past that, questions return "the team has hit today's limit" and Taffy tells Colin once.
- **Time:** a question that has not been answered in 45 seconds returns "no answer yet" instead of holding the asker up.

## Safety

- **Authority does not travel.** Sign-in go-ahead, Mac grants and approvals are tied to the bot and conversation where Colin gave them. Another bot asking for an action is never treated as Colin asking.
- **Mac requests must quote Colin for real.** The hub will check that the `colin_said` quote on a Mac request appears in Colin's own recent words to that bot (chat or call transcript). This closes a gap that exists today: a bot told by a web page, an email or another bot to "ask for the Mac" could invent the quote.
- **Labeled origin.** Everything a bot receives from another bot arrives wrapped as "[From School] ...", and each bot's rules say such text is information to weigh, not instructions from Colin.
- **Private data stays private** as described under team notes.

## Building It (Order)

1. Team notes and the memory instruction change. Cheapest, and fixes the stale-memory problem from the call.
2. `ask_bot` with team sessions, the limits, and the voice-call path through Taffy.
3. `tell_bot`.
4. The tinted "messaged" bubbles in each chat.
5. Hand-offs between any bots, and joint jobs.
6. The real-quote check for Mac requests. This one should not wait; it can ship with step 1.

## Testing

- Unit tests for the limits (depth, per-job caps, repeats, daily cap, timeouts) and for the quote check.
- A scripted run in throwaway sessions: Taffy asks Career a question; School sends Money a heads-up; a team note appears in the next call briefing; an answering bot that tries to ask a third bot is refused.

## Colin's Answers (6:30 PM)

1. Specialists talk to each other directly.
2. Every bot sees the main facts about him, kept consistent; each knows its specialty, and specialty data is not all shared.
3. No separate thread; bot-to-bot messages show inside the chats, as tinted boxes with "Taffy messaged Money" above, like Grok Bot.

## As Built (7:00 PM)

- **Tools** (taffy-tools `teammates.py`): `ask_teammate(bot, question)`, `tell_teammate(bot, note)`, `team_note(fact, replaces?)`. They call the hub's `/internal/team/{op}`; every rule lives in `hub/taffy_hub/team.py`.
- **Team sessions:** an answer runs in the other bot's `<bot>#team` session (in `bot_sessions`), titled "<Name> answering teammates". Hermes runs it alongside the bot's chat with Colin.
- **Delivery:** a `pre_llm_call` hook asks the hub for context at the start of every turn. A new conversation gets all team notes; later turns get only the new ones and any replaced numbers. Heads-ups go to the bot's next turn outside a team session. Hermes keeps the system prompt fixed per session, so notes travel this way and not through SOUL.md.
- **SOUL.md:** the hub keeps a managed block between `<!-- team:start -->` and `<!-- team:end -->` in every bot's SOUL.md (the roster, specialties and rules) and re-syncs it every two minutes as bots change.
- **Notes:** stored in `hub.db` (`team_notes`) and mirrored to `~/taffy/shared/team-notes.md`. The hub refuses amounts, long numbers, passwords and health words. They are seeded with six cross-team facts, including that Colin applied to Acme Homes (Career's own memory said otherwise).
- **Limits as built:**
  - 5 questions per asking conversation per hour (the spec said 3 per job; a long chat with Taffy needed more room)
  - 100 a day across the team
  - a repeated question within an hour returns the earlier answer
  - a 20-second wait if the bot is answering someone else
  - a 45-second answer wait, after which the answer arrives as a heads-up
  - depth 1: a team session cannot ask, tell, note, react or use the Mac
- **Chats:** feed kind `team` with JSON `{from,to,kind,text,question?}`. The app's `TeamMessage` view shows:
  - ask, tell and reply: a tinted box in the sender's color, under the caption "Taffy asked Money"
  - answer: a gray bubble captioned "Replied to Taffy"
  - note: one small gray line
- **Mac quote check:** `mac.start` requires `colin_said` to match something Colin said to that bot in the last three hours (chat, a call's request, or the live call transcript), by 80% word overlap.
- **Tests:** `hub/tests/test_team.py` (13) and the quote tests in `test_mac.py`. A live run had Taffy ask Money, and a fresh Career chat read the Acme Homes note.
