# Guided Voice Setup Design

Date: 2026-10-08. Status: prototype built on branch `voice-setup-v1`, for Colin's review. Facts about Google's
pages, keys and terms were checked on the web and against the live sites today; sources are listed at the end.

## What Did Colin Ask For?

When someone sets up voice on the Mac, a browser opens inside Zero and an overlay walks them through getting a
free Gemini key from Google AI Studio. Everything is grayed out except the one place to click, with a note on
where to click and why. People without a ChatGPT plan use Gemini 3.8 Live with their own free key, so this is
the main way most people will get a voice.

## Can Zero Sign People In to Google Inside Its Own Browser?

No. Zero should not ask people to sign in to Google inside its own window. What Zero can do is draw the same
overlay on top of the person's own browser, where they are usually signed in already. That is what was built.

### What Google Says

- Google's help page for "This browser or app may not be secure" lists four causes. One is browsers that "are
  embedded in a different application" [1].
- Google's OAuth policy says plainly that "embedded webviews implementing or extending WKWebView" do not meet its
  secure browser policy [2].

### What the Test Showed

`tools/voice-setup-probe/probe.swift` loads `aistudio.google.com/apikey` in a plain WKWebView on this Mac, signed
out, and walks up to Google's sign-in form without signing in.

- Signed out, AI Studio sends you straight to Google's sign-in page.
- With WKWebView's default user agent, Google serves its reduced "WebLiteSignIn" page, not the normal one, and
  the page's bot check reports `bgresponse=js_disabled`. Google can tell this is not a real browser
  (`docs/specs/guided-voice-setup/probe-wkwebview-lite-signin.png`).
- With Safari's user agent copied in, Google serves the normal "GlifWebSignIn" page.
- Google answered "Couldn't find this account" for every test address, including likely real ones. The browser
  check usually comes after an existing account is entered, so this test could not reach it. No real account was
  used, and no password was ever typed.

### Why the In-App Browser Is the Wrong Bet

1. It breaks Google's stated rules. Even if copying Safari's user agent gets past the check today, that is the
   exact thing the policy targets. It can stop working on any day, with no warning, for every Zero user at once.
2. It asks people to type their Google password into Zero. Google bans embedded sign-in because an app can read
   what is typed. Zero's privacy story is "we don't have servers"; a password box inside Zero undercuts it.
3. Passkeys and security keys don't work for Google inside another app's web view, so many people could not
   finish at all.
4. A sign-in can't be handed over. Signing in with Safari or the system sign-in sheet (ASWebAuthenticationSession)
   does not give Zero's own web view the Google session cookies, so the in-app browser would still be signed out.

### The Options

| Option | Sign-in works? | Overlay on the real page? | Key caught without typing? | Verdict |
|---|---|---|---|---|
| Zero's own browser (WKWebView) with an injected overlay | Against Google's rules; may be blocked any day | Yes | Yes | No |
| System sign-in sheet, then continue in Zero's browser | Yes, but the session doesn't carry over | Yes | Yes | Doesn't work |
| Overlay on the person's own browser, placed with Accessibility | Yes, usually already signed in | Yes, drawn above the browser | Yes, from the clipboard or the page | **Built** |
| A browser extension that draws the overlay in the page | Yes | Yes | Yes | Too much setup for a one-time task |
| A card of steps beside the browser | Yes | No, words only | Yes, from the clipboard | Built as the fallback |
| Sign in with Google (OAuth), then make the key through Google Cloud's APIs | Yes | Not needed | Yes | Later, maybe (see Open Questions) |

## How the Recommended Approach Works

Zero opens `aistudio.google.com/apikey` in the person's default browser. The Mac bridge (the native AppKit bundle
Zero already loads for Mac control) then lays two windows over the browser window:

- **The dim layer**: a click-through panel the size of the browser window. It grays out everything except a
  rounded cutout around the one thing to click, with a yellow ring and a slow pulse. Clicks pass straight
  through to the browser, so the person clicks Google's real button.
- **The callout**: a small card that never takes focus from the browser. It says "Zero's Guide", the step's
  title, where to click and why, and offers Back, Next and I'm Stuck. It sits under the target, above it, or
  beside it for targets in a left sidebar, with a pointer to the target.

Zero finds each target in the browser's accessibility tree, the same tree VoiceOver reads. Nothing is injected
into Google's page and Zero never clicks for the person. Zero already asks for Accessibility to use the Mac for
the person (`docs/specs/2026-10-07-mac-control-design.md`), so the guide needs no new permission. Without
Accessibility the guide still runs, as one card beside the browser with the steps in words.

It also saves most people a step. They are already signed in to Google in their own browser, so they never see a
sign-in page.

## Facts That Shape the Flow (October 2026)

- **New accounts get a key made for them.** "If you are a new user, Google AI Studio automatically creates a
  default Google Cloud project and API key after you accept the Terms of Service." People who already use Google
  Cloud get no default key and press Create API key [3].
- **Keys changed this year.** Since May 28, 2026 AI Studio makes "auth keys", which start with `AQ.`. Older
  `AIza` keys are being retired and unrestricted ones are rejected [3][4]. Colin's working voice-test key is an
  `AQ.` key of 53 characters (checked by shape only, never printed), and it works with Gemini Live both in the
  address and in the `x-goog-api-key` header.
- **The free tier's data terms.** On unpaid use, "Google uses the content you submit ... to provide, improve, and
  develop Google products" and "human reviewers may read, annotate, and process your API input and output" [5].
  Adding billing stops this. In the EEA, Switzerland and the UK, free use gets the paid data terms [5].
- **Three terms Colin needs to see** (see Open Questions): users must be 18 or older; Google says AI Studio and the
  Gemini API are "for developers building with Google AI models for professional or business purposes, not for
  consumer use"; and apps offered in the EEA, Switzerland or the UK may only use paid services [5].
- **Clipboard privacy on the Mac.** macOS is adding a prompt when an app reads the clipboard without a paste
  action [6]. Zero reads the clipboard once, only right after the clipboard changes while the browser is in front
  on AI Studio's page. The paste box in Zero's own window takes Command-V, which never prompts.

## The Flow, Screen by Screen

### In Zero's Window (Mac)

Opened from Settings, Voice Chat, Set Up Voice (and later from onboarding step 6b, Pick My Voice).

**Give Zero a Voice** (`mac-intro.png`)
> Zero can talk with you on calls. The voice comes from Google, and it's free. Google asks you to get a free key
> first, which takes about two minutes.
>
> **What Happens Next?**
> - Zero opens Google's key page in your browser.
> - Zero dims the page and highlights one thing to click at a time, with a short note on why.
> - If Google asks you to sign in, use your own Google account. Zero can't see what you type.
> - When your key appears, Zero picks it up, checks it, and keeps it on this Mac.
>
> **What Should You Know?**
> - On the free plan, Google may use your calls to improve its products, and people at Google may listen to some
>   of them. Zero leaves money and health details out of what it tells the voice.
> - If you add billing to your key in Google AI Studio, Google stops using your calls. Calls then cost about a
>   cent a minute.
> - Google requires you to be 18 or older.
>
> Buttons: **Get My Free Key**, **I Already Have a Key**, Not Now

**Follow the Highlights** (`mac-guiding.png`, shown while the guide runs)
> Google's key page is open in your browser. Zero is pointing at each click there. When you copy your key, Zero
> brings you back here.
>
> Now: *step title*. Buttons: **Show the Guide**, **Paste My Key Instead**, Stop

**Follow the Card in Your Browser** (`mac-card.png`, without Accessibility)
> Google's key page is open in your browser, with Zero's steps in a card beside it. Zero can highlight each click
> instead if you allow Zero under Accessibility in System Settings.
>
> Buttons: **Open System Settings**, **Start Again**, **Paste My Key Instead**, Stop

**Paste Your Key** (`mac-paste.png`)
> Copy your key in Google AI Studio and paste it here. Zero checks it with Google before saving it.
>
> A hidden-text box ("Paste your key here"). Buttons: **Check Key**, **Show Me Where to Get One**, Back.
> If the text isn't a key: "That doesn't look like a Gemini key. Gemini keys start with AQ. or AIza. Copy the key
> itself, then paste it again."

**Checking Your Key** (`mac-checking.png`)
> Zero is asking Google whether this key works. This takes a few seconds.

**Your Voice Is Ready** (`mac-done.png`)
> Zero checked your key with Google and saved it on this Mac. Calls use Google's voice from now on.
> Key ending in 3fQx. Button: **Done**

**When the Check Fails**

| Google's answer | Screen | Copy | Buttons |
|---|---|---|---|
| Key refused | Google Didn't Accept This Key | The key may be incomplete, or it may have been turned off. Copy it again in Google AI Studio, or make a new one there. | Show Me Again, Paste a Different Key |
| Over a limit | Google Says Try Again Soon | This key has reached Google's free limit for the moment. Wait a minute, then try again. | Try Again, Not Now |
| No connection | Zero Couldn't Reach Google | Check your internet connection, then try again. | Try Again, Not Now |
| Anything else | Google Turned This Key Down | Google said: "*Google's words*" This happens with some work and school accounts, and in places where Google doesn't offer this voice. A personal Google account usually works. | Try Another Account, Not Now |

### On the iPhone

**Set Up Voice on Your Mac** (`iphone-onMac.png`)
> Zero keeps your Google key on your Mac, so voice setup happens there. Open Zero on your Mac and choose Settings,
> then Set Up Voice.

### In the Browser (the Overlay)

Each step is one click. The callout says what to click, using Google's own label as written on the page, and why.

| Step | Shown when | Highlights | Callout title and text | Moves on when |
|---|---|---|---|---|
| 1 | On Google's sign-in page | The "Email or phone" box (`#identifierId`) | **Sign In to Google.** Sign in with the Google account you want Zero's voice to use. A personal account works best. Zero can't see what you type here. | The browser reaches AI Studio |
| 2 | AI Studio's first-visit terms box is open | The Terms of Service checkbox | **Accept Google's Terms.** Check this box to accept Google's terms. Google asks everyone to do this once before making a key. The box about emails from Google is up to you. | The box is checked |
| 3 | Same | Continue | **Continue.** Click Continue. This saves your answer and opens AI Studio. You only do this once. | The button is gone |
| 4 | Not yet on the keys page | Get API key in the left menu | **Open Your Keys.** Click Get API key. It opens the page that lists your keys. If you are new to AI Studio, Google has already made one for you. | The address is the keys page |
| 5 | No key is listed | Create API key | **Make a Key.** Click Create API key. You don't have a key yet, so this makes one. It doesn't change anything else on your account. | The new-key dialog opens |
| 6 | The new-key dialog is open | Create key | **Name It and Create.** Click Create key. You can type Zero as the name first, so you know what the key is for later. If Google asks for a project, keep the one it suggests or choose Create project. | A key appears, or the dialog closes |
| 7 | A key is listed | The Copy button | **Copy Your Key.** Click Copy. Zero picks the key up from your clipboard, checks it with Google, and keeps it on this Mac. You won't need to paste it anywhere. Button: **I Copied It** | Zero has the key |

Steps that don't apply skip themselves, so a person who is signed in and already has a key sees two steps: Open
Your Keys and Copy Your Key.

**When Google's page has changed.** If the target isn't found within four seconds, the callout says:
> **Zero Can't Find This Step.** Zero can't find "Create API key" on this page. Google may have changed it. If you
> can see it, click it and Zero will catch up. If not, choose I'm Stuck.

Zero catches up on its own because each step moves on when the page changes, not when a click lands
(`overlay-09-not-found.png`).

**I'm Stuck** opens a list in the callout (`overlay-10-im-stuck.png`): Find It Again, Skip This Step, Open the Key
Page Again, Paste My Key Instead, Stop Setup, and the note "Work and school accounts are sometimes blocked from
making keys. A personal Google account usually works."

**Wrong tab.** If the browser shows another page, the cutout goes away and the callout says:
> **Back to Google's Key Page.** Zero is waiting for Google AI Studio. Switch back to its tab, or choose I'm Stuck
> to open it again.

**The card, without Accessibility** (`overlay-11-card-mode.png`):
> **Get Your Free Key**
> 1. If Google asks, sign in and accept its terms.
> 2. Click Get API key on the left.
> 3. If no key is listed, click Create API key, then Create key.
> 4. Click the Copy button next to your key. Zero picks it up from your clipboard.
>
> Button: **I Copied It**

## Targeting Google's Pages

The script is data (`ios/Shared/VoiceGuide/GuideScript.swift`). Each step lists:

- **Targets** by what a person sees: the words on a button or its accessible label, compared without regard to
  case, curly quotes or spacing, plus allowed roles (button, link, checkbox) and a stable page id where Google has
  one (`identifierId` on the sign-in page). Each target lists several wordings in order, so "Get API key" also
  matches "API keys" if Google renames it.
- **Pages** where the step applies, by host and path. The address's query is ignored on purpose: the sign-in
  page carries `continue=https://aistudio.google.com/apikey` in its query, and an early version took that to mean
  the person was already on AI Studio.
- **Done when** and **skip when** conditions: address, words present on the page, target present, checkbox
  checked, key found.
- A preference for matches inside an open dialog, because a dialog covers the page behind it.

Keys are only looked for on AI Studio's own pages, never on sign-in pages, and password boxes are never read.

### Calibrating the Script

The signed-in labels come from Google's docs (updated 2026-10-06) and recent walk-throughs, not from seeing the
signed-in site, because no Google account was used. One signed-in run pins them:

1. Build the tools: `tools/voice-setup-probe/build.sh`.
2. Open AI Studio in a browser that is signed in, then run the harness on it, with no autopilot:
   `/tmp/zero-guide-bin/harness --pid <browser pid> --shots /tmp/calibrate --seconds 300`.
3. Click through by hand. For every step the harness saves a picture and a `.txt` list of every element's role,
   page id and words, with anything key-shaped replaced by `[key]`.
4. Put the real labels into `GuideScript.geminiKey()`. Test again on a brand-new Google account if possible, since
   that path (terms box, default key) is the one most people take.

## Catching, Checking and Saving the Key

- **Catching.** Two ways, both automatic: the key appears in full on AI Studio's page (read from the
  accessibility tree), or the person clicks Copy and the clipboard changes while the browser is in front on AI
  Studio. Either way the key must match `AQ.` plus 40 to 80 letters, digits, `-` or `_`, or the older `AIza` form.
  The person never pastes it. The paste box is the fallback.
- **Checking.** `GeminiKeyCheck` opens a Gemini Live session with `gemini-3.8-live`, sends the setup message, waits
  for `setupComplete`, and closes. No audio is sent, so it uses no minutes. The key goes in the `x-goog-api-key`
  header, never in the address. Tested today: Colin's real key returned "works" in 0.4 s; a made-up `AIza` key
  and the practice key returned "rejected" in 0.2 to 0.3 s.
- **Saving.** Only after the check passes, into the Mac's keychain (`GeminiKeyStore`, account `geminiKey`,
  readable after first unlock, this device only), through the app's existing `Keychain` helper. The key is never
  logged or shown; screens show only "Key ending in" and the last four characters.
- **Where the key travels.** The bridge hands the key to Zero's window through a block passed to a separate
  selector (`guide:events:`), not `handle:reply:`. The hub can't start the guide or receive the key.

## What Was Built

| File | What it does |
|---|---|
| `ios/Shared/VoiceGuide/GuideScript.swift` | The step script, the key pattern, text matching and the callout's words. Foundation only, shared by the app and the bridge |
| `ios/MacBridge/VoiceGuide.swift` | The guide engine: opens the page in the browser, reads the accessibility tree twice a second, follows the target at 30 frames a second, decides the step, catches the key, places the panels |
| `ios/MacBridge/GuideOverlayViews.swift` | The dim layer, the callout card, the buttons and the pointer, in AppKit and Core Animation |
| `ios/MacBridge/TaffyMacBridge+Guide.swift` | The bridge's `guide:events:` entry point |
| `ios/Taffy/Voice/GeminiKeyCheck.swift` | The Gemini Live handshake check |
| `ios/Taffy/Views/VoiceSetupView.swift` | Zero's screens, the key store, and the iPhone's pointer to the Mac |
| `ios/Taffy/Model/MacLink.swift` | `voiceGuide(_:events:)`, and the bridge's load error is now logged |
| `ios/Taffy/Views/RootView.swift` | Set Up Voice in Settings; a debug launch flag for each screen |
| `tools/voice-setup-probe/` | The WKWebView sign-in probe, a bare WebKit test browser, the guide harness, and the practice copy of AI Studio |

## How It Was Tested

- **The guide engine** ran in the harness over the test browser, a bare WebKit window with Safari's user agent
  and a fresh signed-out session, so Colin's own Safari was never touched. Autopilot pressed each highlighted
  element like a person would.
  - Practice page, new account: terms, Continue, Get API key, Copy; the practice key was caught from the
    clipboard (`overlay-02` to `overlay-05`).
  - Practice page, existing Cloud user: Create API key, Create key, Copy; caught (`overlay-06` to `overlay-08`).
  - Practice page with Create API key renamed to New key: the "can't find" callout (`overlay-09`).
  - I'm Stuck open (`overlay-10`); card mode (`overlay-11`).
  - **Google's real sign-in page**, reached from `aistudio.google.com/apikey` while signed out: the Email or phone
    box was found by its page id and highlighted, with the callout above it so Google's Next stays visible
    (`overlay-01-sign-in-real-google.png`).
- **End to end in the Catalyst app**: Zero's window started the guide through the real bridge, the guide opened the
  practice page in the test browser and highlighted Copy, the copy reached Zero, Zero checked the key with Google,
  and the window showed Google Didn't Accept This Key, which is right for the fake key (`e2e-1` to `e2e-3`).
- **Builds**: Mac Catalyst and the iOS simulator (an iPhone 17 simulator made for this and deleted after), derived
  data in `ios/build/`. Screen pictures: `mac-*.png` and `iphone-*.png`. Debug builds used a separate bundle id
  and ad-hoc signing so they could not touch Colin's installed Zero or its pairing.

All pictures are in `docs/specs/guided-voice-setup/`.

## Findings Along the Way

- **The bridge must not link SwiftUI.** The first overlay was SwiftUI. It worked in the harness, a plain macOS
  process, but inside the Catalyst app SwiftUI resolves to its iOS build, which has no `NSHostingView`, and the
  whole bridge failed to load ("Symbol not found: NSHostingView"). That would also have switched off Mac control.
  The overlay is now AppKit and Core Animation, and the bridge links no SwiftUI (checked with `nm`).
- **A dialog that opens right after another closes can be missing from WebKit's accessibility tree.** On the
  practice page, the "API key created" dialog showed on screen but not in the tree, whether shown with CSS or
  added to the page, and whether pressed by Accessibility or by a real mouse click. Opened on page load, it was
  there. The guide still finished, because Copy is caught from the clipboard, but the highlight pointed at the
  Copy button behind the dialog (`overlay-08-copy-behind-dialog.png`). Check this on the real site during
  calibration. If it happens there, the copy step can point at the dialog's area instead of a single button.
- **Chrome builds its accessibility tree for web pages only when asked.** The guide sets `AXManualAccessibility`
  on the browser when it starts. Chrome isn't installed on this Mac, so this was not tested.

## Risks

- **Google changes AI Studio.** Each target lists several wordings, steps move on when the page changes, and the
  "can't find" callout, I'm Stuck and the paste box catch the rest. A renamed button costs one step of guidance.
  The setup still finishes.
- **Labels not yet seen signed in.** One calibration run fixes this. See Calibrating the Script.
- **Firefox and other browsers** expose less to Accessibility. They get the card.
- **The clipboard prompt** may appear once on future macOS versions when Zero reads the copied key. The callout
  already says "Zero picks the key up from your clipboard", so the prompt won't come as a surprise.
- **Several Google accounts in one browser.** AI Studio uses the browser's current account. The sign-in callout
  says which account to use, but switching accounts inside AI Studio isn't guided.

## Open Questions for Colin

1. **Google's terms say AI Studio and the Gemini API are "for professional or business purposes, not for
   consumer use."** Having every Zero user make their own free key is a consumer use. Is that acceptable, or should
   the Gemini tier wait for a clearer path (for example, Zero's voice through a ChatGPT plan, or a paid key Zero
   recommends)?
2. **The EEA, Switzerland and the UK.** Google's terms allow only paid services for apps offered there. Should
   Zero skip the free path in those places and ask for a key with billing?
3. **The privacy line in the intro.** "Zero leaves money and health details out of what it tells the voice" comes
   from the voice design's trimmed free-tier briefing, which isn't built yet. Keep the line and build the trim,
   or drop the line?
4. **Starting from the iPhone.** Onboarding's Pick My Voice step is on the iPhone. Should the phone ask the Mac to
   open the guide over the link, so the person just turns to their Mac, rather than telling them to open Settings?
5. **A Google sign-in that makes the key for them.** Signing in with Google and letting Zero create the project and
   key through Google Cloud's APIs would remove every click after sign-in. It needs Google's full Cloud access
   permission, an app review by Google, and a consent screen that sounds alarming ("see, edit, configure and
   delete your Google Cloud data"). Worth exploring later?
6. **One signed-in calibration run.** It needs a Google account. Would you run it with yours (the harness never
   prints or saves a key), or should a fresh test account be made for it?

## Sources

1. Google Account Help, "Couldn't sign you in" / "This browser or app may not be secure":
   https://support.google.com/accounts/answer/7675428
2. Google Developers Blog, "Upcoming security changes to Google's OAuth 2.0 authorization endpoint in embedded
   webviews": https://developers.googleblog.com/2021/06/upcoming-security-changes-to-googles-oauth-2.0-authorization-endpoint.html
3. Gemini API docs, "Using Gemini API keys" (last updated 2026-10-06): https://ai.google.dev/gemini-api/docs/api-key
4. Google AI Developers Forum, threads on `AQ.` auth keys, including Google's reply of 2026-07-20:
   https://discuss.ai.google.dev/t/account-restricted-to-aq-keys-all-return-401-access-token-type-unsupported-on-generativelanguage-googleapis-com-requesting-fix-aiza-restoration/175424
5. Gemini API Additional Terms of Service: https://ai.google.dev/gemini-api/terms
6. Michael Tsai, "Pasteboard Privacy Preview in macOS 15.4": https://mjtsai.com/blog/2025/05/12/pasteboard-privacy-preview-in-macos-15-4
