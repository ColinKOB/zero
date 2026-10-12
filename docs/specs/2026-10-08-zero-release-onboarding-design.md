# Zero Release and Onboarding Design

Status: **draft for Colin's review** (2026-10-08). Nothing here is built yet except the concept art and a
throwaway motion study. Approve the design sections, then this becomes the spec for the implementation plans.

## What Are We Making?

Zero for other people: someone downloads the iPhone app, meets their first agent, sets Zero up on their Mac
with help from Claude or ChatGPT, and within minutes has a personal team of agents ("Zips") that already knows
something about them. It should feel like setting up a friend and a coworker, closer to the opening of a good
video game than to a settings screen.

### What Colin Decided

| Question | Decision |
|---|---|
| Scope | Design the whole release path now |
| Computer | Macs only. Each person's Mac is their Zero home |
| AI | The person's own Claude or ChatGPT plan powers their Zips; Claude or ChatGPT also does the install |
| Our servers | None. "We don't have any servers" stays true |
| Look-around | Medium: names of files and folders, apps, contact card, calendar, recent email subjects and senders, a few files the agent picks by name. Each source has its own switch |
| Colin's own setup | Kept. The phone can pair with a Mac home or a server home like alien |
| Architecture | Approach A: the Zero Mac app contains the whole engine and runs it in the background |
| Characters | Sharp, rendered 3D plush characters (made with OpenAI's image model), not drawn in code. Two separate styles, plush and flat, never blended |
| Zero's starting look | Look A: the yellow plush O with round glasses, open eyes and an orange and cream striped scarf |
| Choosing a look | A carousel of about 20 bodies under the agent; add-ons in a column of clean boxes on the left |
| App icon | Done: the sunflower yellow Ø on deep charcoal, redrawn as exact geometry (`ios/scripts/make-icon.py`) |
| Install path | A normal download of Zero for Mac is the main path; "ask Claude or ChatGPT to set it up" is the second option |
| Flat style | A full set of 20 flat bodies, matching the plush shapes |
| Voice | Chosen during onboarding, and each Zip can have its own voice; see "Voice" below for who pays |
| Call model | Gemini 3.8 Live (fast) by default. The thinking version took about 18 s to answer in Colin's test |
| Characters | Modeled, furred and animated in Blender and rendered with Cycles; the image-model art is the reference |

### What Fable's Review Changed

Fable (Claude Fable 5.1) reviewed the plan on 2026-10-08. Its corrections are adopted below:

1. **Claude plans.** Anthropic forbids third-party apps from using a person's Claude sign-in on their behalf.
   The allowed path is the person signing in to the official, unmodified Claude Code on their own Mac, which
   the engine then runs (as alien does today). Zero never reads, stores or relays Claude credentials.
   Anthropic paused, not cancelled, a cap on third-party use of subscriptions, so Zips must be frugal and
   "use your own API key" is a first-class option. Sources: code.claude.com/docs/en/legal-and-compliance,
   support.claude.com/en/articles/15036540.
2. **ChatGPT plans.** OpenAI's "Sign in with ChatGPT" lets open-source apps use a person's plan, with an
   official path for `codex app-server`. Zero's engine is open source to qualify. Voice on the ChatGPT plan is
   unverified until tested. Sources: developers.openai.com/siwc/quickstart, /siwc/token-sharing-open-source.
3. **Macs sleep.** A closed MacBook stops. The app says plainly "Zero works while your Mac is awake," holds a
   power assertion only during tasks, and the phone stays useful while the Mac sleeps (history, queued
   messages, a clear "your Mac is asleep" state).
4. **No Linux desktop on a Mac.** Each Zip uses "Zero's window," a separate browser profile on the Mac.
   Using the person's own apps stays behind per-task approval, as today.
5. **Transport.** CloudKit is too slow for streaming chat. Same network: direct WebSocket found by Bonjour,
   with a certificate pinned at pairing. Away: a peer-to-peer WebRTC data channel whose setup messages travel
   through CloudKit; CloudKit as a slow mailbox only when peer-to-peer fails. Notifications: CloudKit
   subscriptions, so Apple delivers them and no notification key ships to anyone.
6. **Privacy copy.** "Stays on your Mac, except what your agent sends to Claude or ChatGPT."
7. **Character risk.** OpenAI's yellow dots character is recognized by round glasses over closed eyes plus a
   black bow tie. Zero never ships that combination. Look A (open eyes, scarf, no bow tie) is clear of it;
   the add-on set has no bow tie, beret or sunglasses.

## Architecture

```
iPhone app ──(home Wi-Fi: WebSocket, Bonjour)────────┐
     │      ──(away: WebRTC data channel)────────────┤
     │      ──(setup messages and notifications: the person's iCloud)
     ▼                                               ▼
                      Zero for Mac (Developer ID, notarized, Sparkle updates)
                      ├─ app: onboarding on the Mac, permissions, menu bar status
                      ├─ engine (SMAppService agent, KeepAlive): embedded Python, Hermes, the hub API
                      ├─ Claude Code (official, signed in by the person) and/or Sign in with ChatGPT
                      ├─ Zero's window: a separate browser profile for Zips
                      └─ Mac bridge: the person's own apps, only with per-task approval
```

- **Engine.** Hermes and the hub, trimmed for one person: the lead Zip plus Zips made from templates.
  Embedded python-build-standalone, every binary signed with the hardened runtime, notarized and stapled.
  Updates ship through Sparkle, with Claude Code versions pinned and tested.
- **Server homes.** The phone can also pair with a server home such as alien (today's setup, APNs from the
  hub). Hidden behind an advanced option until Mac homes are stable.
- **Open source.** The engine is published, which also qualifies Zero for Sign in with ChatGPT.

## The Onboarding, Screen by Screen

Every screen has a white background, one idea, and the agent on stage. Copy is plain and warm.

1. **Meet your agent.** The agent hops up from the distance in three soft hops, growing until it fills the
   middle third, settles with a wobble and blinks. "Meet your agent." appears above it in large type.
   Underneath, a carousel of bodies; on the left, a column of add-on boxes (white, faint gray border, one
   accessory each). Changing the body swaps the character with a small hop.
2. **Name it.** "What do you want to name it?" over a "HELLO my name is ___" badge. As the name is typed,
   the agent watches; on Done it does a happy squint and a hop.
3. **Its first words.** Game-style dialogue in a speech bubble with a typewriter effect and a talking
   mouth: "Hey, I'm Zero! What should I call you?" Then work, where they live, age, family. Each question
   has an answer field and a Skip. This part is scripted, because no AI is available until the Mac is set up;
   if Apple's on-device model is available, it phrases the replies so they feel alive.
4. **Your computer.** "Do you have a Mac nearby? That's where I'll live and work." The main button sends a
   download link (AirDrop, Messages or a short web address). A smaller option copies a setup prompt to paste
   into Claude or ChatGPT on the Mac. The Mac app signs the person in to Claude or ChatGPT and shows a
   six-character access code.
5. **The code.** They type the code into the phone; the agent celebrates. From here the real agent talks.
6. **Why we ask.** A short, friendly explainer before any permission: what each permission is for, and
   "We don't even have servers. What I learn stays on your Mac, except what I send to Claude or ChatGPT to
   think." Then the switches, one per source, and Apple's prompts one at a time.
6b. **Pick my voice.** The agent speaks a line in each voice; the person taps the one they like. The voice
   options depend on what they signed in with (see "Voice").
7. **Looking around.** "Let me take a look at your Mac to see how I can help. You can stay, or turn on
   notifications and I'll tell you when I'm done." Notification permission is asked here, where it has an
   obvious reason.
8. **What I found.** About ten minutes later, a notification and a message: who they seem to be ("You look
   like you work in construction in Ohio and have two kids") and, under "Here are some Zips I'd start
   with," two to four templates, each with one line on what it does. A short note explains Zips: "Each Zip
   does one job. Talk to it directly, or tell me and I'll hand it out."

## Characters

- **Bodies.** About 20 plush bodies, straight-on, one camera angle and lighting, so add-ons fit any of
  them. First set made (round 3): O, star, moon, bolt, drop, cube, pill, hexagon, gem, rounded square,
  mushroom, bean, arch, flower, dome, leaf, bell, plus, pebble, gumdrop. Two near-duplicates to replace
  (cube or rounded square; pebble or gumdrop). A matching flat set follows for the flat style.
- **Add-ons.** 18 made with transparent backgrounds: round glasses, square glasses, scarf, headphones,
  beanie, hard hat, graduation cap, baseball cap, chef's hat, top hat, crown, flower crown, sprout,
  earmuffs, necktie, bandana, headset, goggles.
- **Layering.** Hats and glasses are layered using per-body anchor points (head top, eye line, neck line
  and width). The layering test showed wraparound items (scarves, headphones) look pasted on, so those are
  rendered per body with the image editor from that body's image and stored as their own layers.
- **Expressions.** Each body gets blink, happy and talk frames made by editing its reference image. The
  test changed only 0.7% of pixels, all in the face, so swaps read as real expressions. The blink frame
  needs a redo so it differs from the happy squint.
- **Asset pipeline.** Scripts in `docs/design/zip-concepts/` (generate, add-ons, frames). Final assets
  are made with `gpt-image-2.5-sunburst` at high quality with transparent backgrounds, checked by eye,
  and shipped in the app's asset catalog. Nothing is generated on a person's phone.

## Animation

Video generation is not used: OpenAI's video endpoint returns 404 for this account even though `sora-2`
is listed, and video can't carry add-ons. Motion is done in the app on top of the rendered stills, so
add-ons move exactly with the body:

- **Walk-up:** scale from 0.26 to 1 with ease-out over about 2.4 s in three hops; stretch going up, squash
  on landing; a slight 3D tilt per hop; a ground shadow that grows and fades with height.
- **Idle:** breathing (about 1% scale at 0.3 Hz), a slow sway, blinks every few seconds, glances.
- **Reactions:** happy squint and hop on naming, talking mouth while its line types out, a pop when an
  add-on goes on.
- **Reduce Motion:** the agent appears in place and only blinks.

Reference: `docs/design/zip-concepts/motion/walkup-study.mp4` (throwaway study, not app code).

## Voice

Live calls need a speech-to-speech model, and only some sign-ins include one:

| Person has | Voice used | Cost to them |
|---|---|---|
| A ChatGPT plan | OpenAI realtime through their plan (to be verified with Sign in with ChatGPT) | Included |
| No ChatGPT plan | Google Gemini 3.8 Live (GA 2026-09-15) with their own free Gemini key | Free tier, then about $0.02 a minute |
| Neither, or offline | Apple's on-device speech and voices, with their Claude plan doing the thinking | Free, sounds more robotic |

For comparison, OpenAI's `gpt-realtime-2` on the API is about $0.30 a minute in typical use. Gemini 3.8 Live
is about 13 times cheaper and its thinking version leads a public speech-to-speech quality index. Its free
tier may let Google use the audio to improve its products, which the voice picker says plainly. Sessions
without compression cap at 15 minutes, so long calls turn compression on.

**Test, 2026-10-08** (`docs/research/voice-test/live_test.py`, a spoken question streamed at real-time pace):
`gemini-3.8-live` started answering 1.22 to 1.46 s after the speaker stopped, including its own end-of-speech
detection, across the Kore, Puck, Charon and Aoede voices, and the answers were short, natural and on point.
`gemini-3.8-live-extended-thinking` (thinking level low) answered as fast with a brief "let me look" and keeps
reasoning in the background, so the client must wait for the session to go idle rather than for turnComplete.

### How Zero Talks on a Call

Colin's test on 2026-10-08 showed the voice must act like the agent, not a chatbot:

- **Answer specifically.** When facts are missing, make the likely assumption, say it in a few words, answer,
  then ask the one question that would change the answer most ("Assuming you file single with the standard
  deduction, about seventy-one thousand. Do you put anything in a 401(k)?").
- **Look things up instead of declining.** The voice model calls Zero's tools: a web lookup, a calculator, and
  (in the real app) a hand-off to the lead Zip for anything that needs memory, accounts or longer work. It
  says "one sec" first so the line never goes quiet.
- **Move things forward.** Offer the next step, point out what he might be missing, recommend one option,
  never end on a dead stop.
- **Know the person.** The real call gets the lead Zip's notes about the person, so "what do you know about
  me" has a real answer. Gemini's own Search and code tools are not used: Search needs a paid key and code
  execution isn't supported on the fast model.

### A Voice for Each Zip

Each Zip can have its own voice. Onboarding picks the lead Zip's voice; New Zip and Change Look offer a voice
row with a short sample line in each. Default: every Zip uses the lead's voice until changed.

## Characters in Blender

The rendered concept art set the look; the living characters are built in Blender (5.2, scripted) and
rendered with Cycles. The spike (`docs/design/blender/zero_spike.py`) builds Zero from a script: an upright O
with real fur strands, glossy bead eyes, a smile and round glasses, on a white studio sweep, with four
animations (idle, happy, talking, walk-up). Lessons from the spike:

- EEVEE's hair looked like sandpaper; Cycles hair reads as plush (about 10 to 25 s a frame on this Mac).
- The hair shader turns saturated yellow olive; plain surface shading on the strands keeps the true color.
- Each animation is rendered once per character and played in the app as a short video loop with transparency;
  add-ons are modeled in Blender too and rendered in place, so they move with the body.

## The Icon

Done on 2026-10-08: the sunflower yellow Ø on deep charcoal Colin picked, redrawn as exact geometry by
`ios/scripts/make-icon.py` (light, dark and tinted).

## Risks

- **Claude quota:** the subscription cap could return. Keep Zips frugal and offer an API key option.
- **Sleeping Macs:** set expectations in the onboarding; never promise work while the lid is closed.
- **App Review:** needs a demo mode that runs without a Mac, and a clear privacy story.
- **Prompt injection:** reading mail and files then acting is risky; keep per-task approval for the Mac,
  and never send, buy or delete without the person's yes.
- **Revenue:** with no servers there is no subscription to sell through us; likely a paid app or a one-time
  Mac license. Open question.

## Build Order (Separate Implementation Plans)

1. **Characters and animation in the app:** the asset set, the agent view with all motion, the body
   carousel and add-on boxes. Replaces Change Look. Works with Colin's current setup.
2. **Phone onboarding:** screens 1 to 8 against a server home first (Colin's alien), with a demo mode.
3. **Zero for Mac as a home:** embedded engine, sign-ins, access codes, Bonjour and WebRTC transport,
   CloudKit setup messages and notifications.
4. **Look-around and first recommendations:** the sources, switches, the report and the follow-up.
5. **Release:** icon, App Store and notarization, the open-source engine, the website and download.

## Open Questions for Colin

1. Pricing: paid app, one-time Mac license, or free for now?

## Decisions From Colin (Oct 8 Evening)

- **Connect Google** appears in onboarding only when the person picks a suggested Zip that uses Gmail, Calendar or Drive.
- **ChatGPT-only people:** build the path that connects Google through ChatGPT's own connectors next, so Connect Google is not only for Claude plans.
- **Other connectors:** Zero lists every connector the person already has in Claude (for example Notion or Slack), and Zips can use them with the same permission cards.
- **Banks:** people on the paid Zero Cloud plan get bank data through SimpleFIN Bridge, set up by Zero on the developer side. People running the free, self-hosted version choose SimpleFIN or Plaid themselves.
- **Guided voice setup:** merge it and run a QA pass.
- **Zero Cloud:** keep building the prototype within the $10 cap.
- **Permission judge:** the paid judge is back on for the Zips.
- **Zip sandbox:** plan moving the Zips' terminal and code tools into Docker containers on alien, then build it after Colin approves the plan.
