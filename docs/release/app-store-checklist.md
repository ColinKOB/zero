# App Store and TestFlight Checklist for Zero

Date: 2026-10-08. Status: groundwork only. Nothing has been built, uploaded or submitted.

This checks the app in `ios/Taffy` (bundle id `com.example.Taffy`, team `YOUR_TEAM_ID`, version 0.1.0 build 1) against Apple's rules as they stand today. The App Review Guidelines page says "Last Updated: June 8, 2026" and the numbering below follows that page (https://developer.apple.com/app-store/review/guidelines/). The June 2026 changes were to the introduction, 1.2, 4.3(a), 4.3(b) and 4.5.3 (https://developer.apple.com/news/?id=a233fmpw), so none of them touch Zero. Items I could not confirm from Apple's pages this session are marked "not verified".

## What Is Ready and What Is Missing?

| Area | Ready | Missing |
|---|---|---|
| Identity | Bundle id, team, display name "Zero", icon (Oct 8), alternate icons listed in `project.yml` | Check that the name "Zero" is free in App Store Connect; in-app text still says "Taffy" in places |
| Permissions | Four strings and one background mode, all tied to real features | Two strings need rewording (see the permission table); no explicit consent screen for sending data to an AI company |
| Privacy | No analytics or crash SDKs in the app or hub; Keychain storage; transcript off switch | Privacy policy URL, privacy manifest, App Privacy answers entered, a delete-everything action |
| Review access | Simulator-only fixture mode exists | A demo mode that ships in the release build, review notes, a reviewable path without a Mac |
| Build | iPhone and Mac Catalyst both compile | Release signing and archive never run; Mac build cannot go to the Mac App Store as it stands |
| Legal | Spec raises the terms questions | Answers from Anthropic, OpenAI and Google (`terms-questions.md`) |

## Which Builds Go Where?

**iPhone and iPad.** TestFlight first, then the App Store. This is the main path.

**Mac.** The Mac Catalyst target embeds `TaffyMacBridge`. That bundle runs shell commands through `/usr/bin/sandbox-exec`, posts mouse and keyboard events with `CGEvent`, captures the screen with ScreenCaptureKit and checks Accessibility trust. Mac App Store apps "must be appropriately sandboxed" (2.4.5(i)) and must update through the store (2.4.5(vii)). The bridge cannot do either. TestFlight for Mac uses the same store signing, so it needs the sandbox as well.

The release design already points the same way: Zero for Mac ships outside the store with Developer ID, notarization and Sparkle updates. Recommendation:

- [ ] Ship the iPhone and iPad build through the store. Leave the Mac platform off the App Store version.
- [ ] Ship Zero for Mac as a separate notarized download (the design's build step 3 and 5).
- [ ] If a store Mac app is wanted later, build a sandboxed thin client without the bridge, with `com.apple.security.app-sandbox`, `com.apple.security.network.client` and `com.apple.security.device.audio-input`. Today's `Taffy-Mac.entitlements` has the audio and keychain entries and the hardened runtime note, but no sandbox entitlement.
- [ ] Decide whether `TARGETED_DEVICE_FAMILY: "1,2,6"` should keep iPad. Listing iPad adds iPad screenshots and the four-orientation Info.plist entry that is already present (2.4.1 encourages it).

## What Does TestFlight Need?

TestFlight limits: up to 100 internal testers who are App Store Connect users, up to 10,000 external testers, builds last up to 90 days, and the first build given to an external group goes through Beta App Review (https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview).

- [ ] Apple Developer Program membership active for team `YOUR_TEAM_ID`, and the App Store Connect app record created with the bundle id.
- [ ] Archive with Xcode 26 or later and the iOS 26 SDK. Apple has refused older toolchains since April 28, 2026 (https://developer.apple.com/news/upcoming-requirements/). Check that page again on upload day.
- [ ] Release signing resolves `aps-environment`. `Taffy.entitlements` says `development`. A distribution-signed archive normally rewrites it to `production`, and `Push.swift` already uses `production` for non-debug builds. Verify in the archive's entitlements. The Mac file uses the key `com.apple.developer.aps-environment`, which also needs checking.
- [ ] Beta app description, feedback email and a privacy policy (the required Test Information list), and review contact details.
- [ ] Internal testing first. It needs no review.
- [ ] Beta App Review notes that explain the demo mode (below), because a reviewer has no Mac running Zero.
- [ ] Remove the hard-coded home address. `HubClient.defaultServer` is `https://alien.tail0000.ts.net:6443`, Colin's private tailnet name. A shipping build should have no default.
- [ ] Decide how testers get the Mac side. The Mac download link and setup prompt in onboarding (`OnboardingLinks`) must point at something real.
- [ ] Export compliance. `Info.plist` has no `ITSAppUsesNonExemptEncryption`. The app uses HTTPS and WebRTC's standard encryption, which is normally exempt. Set the key to `false` if that holds, or answer the questionnaire on every upload. Confirm with the lawyer.

## What Does the Store Listing Need?

- [ ] **Privacy policy URL**, required (https://developer.apple.com/app-store/app-privacy-details/) and linked inside the app where it is easy to find (5.1.1(i)). The draft is `privacy-policy-draft.md`. The app has no link to a policy today.
- [ ] Support URL and a contact address that works.
- [ ] Screenshots for each device size that is listed, taken from the demo mode. They must show the app in use (2.3.3).
- [ ] Description and "What's New" that match what ships. The release design promises things that are not built (Zero for Mac, the 20 bodies, Gemini calls). Describe only what the build does (2.3.1, 2.3.12).
- [ ] Age rating. Answer Apple's updated questionnaire. Zips can browse the web and the screen view lets a person drive a browser, and Apple lists "Unrestricted Web Access" at 16+ (https://developer.apple.com/help/app-store-connect/reference/app-information/age-ratings-values-and-definitions). Expect 16+. The questionnaire needed answers by January 31, 2026.
- [ ] Category: `public.app-category.productivity` is already set.
- [ ] Name. "Zero" is short and probably contested. Check availability in App Store Connect and prepare a fallback such as "Zero: Personal Assistant". Not verified.
- [ ] Trader status for the EU storefronts. Not verified this session; check the account page in App Store Connect.
- [ ] Pricing. Free for v1. See "Payment" below before adding anything paid.
- [ ] Content rights: the plush characters were made with OpenAI's image model, and the add-ons and bodies must be cleared for commercial use. Confirm OpenAI's output terms permit this. Keep the design rule that Zero never combines round glasses over closed eyes with a bow tie (5.2.1 copycat).
- [ ] Names and logos: Anthropic allows saying in plain text that a product runs Claude Code, but not using its names or logos in the product's own name or logo (https://code.claude.com/docs/en/legal-and-compliance). Do the same for ChatGPT, Codex and Gemini. Use plain text only.

## What Are the App Privacy Answers?

Apple defines "collect" as transmitting data off the device "in a way that allows you and/or your third-party partners to access it for a period longer than what is necessary to service the transmitted request in real time". Third-party partners are "analytics tools, advertising networks, third-party SDKs, or external vendors" (https://developer.apple.com/app-store/app-privacy-details/).

**Facts from the code.**

- No analytics, crash reporting or advertising SDK appears in the app or the hub. The only package is WebRTC (`stasel/WebRTC` 154.0.0).
- The app talks to the person's own hub, which runs on the person's own computer.
- Call audio travels from the phone straight to OpenAI's realtime voice (`RealtimeLink`). Gemini calls are designed but not yet in the app; the app only checks a saved Gemini key with a test session to Google.
- Messages, files and mail that a Zip reads go from the person's computer to Anthropic or OpenAI, not from the app.
- Notification text goes through Apple.

**Recommended label for v1 (conservative).** Zero's developer receives nothing, so a "Data Not Collected" label can be defended, because the hub is the person's own and the AI companies are accounts the person holds. The risk is that a reviewer reads audio sent to OpenAI as collection by a third-party partner, and 5.1.2(i) asks for plain disclosure of sharing with third-party AI. A label that shows the audio is the safer first submission. Ask the lawyer to choose, and keep the policy and label identical.

| Data type | Collected? | Linked to identity? | Used to track? | Purpose | Why |
|---|---|---|---|---|---|
| Audio Data | Yes (conservative) | Yes, to the person's own OpenAI or Google account | No | App Functionality | Call audio goes from the phone to the voice provider |
| Other User Content | Yes (conservative) | Yes | No | App Functionality | Messages go to the person's hub and from there to the AI company; the phone sends only to the hub |
| Contacts, Location, Health, Financial Info, Purchases, Browsing History, Search History, Sensitive Info | No | n/a | n/a | n/a | The iPhone app requests none of these. The Mac look-around reads a contact card and calendar from the Mac app, which is outside the store |
| Identifiers (User ID, Device ID) | No | n/a | n/a | n/a | The push token and device name go to the person's own hub only |
| Diagnostics | No | n/a | n/a | n/a | Call diagnostics (`system_call`, `route`, `timing`) go to the person's own hub and are deleted after 30 days. No crash SDK |
| Usage Data | No | n/a | n/a | n/a | No product analytics |

Tracking: none, so no App Tracking Transparency prompt and no `NSUserTrackingUsageDescription`.

**Alternate answer.** If the lawyer prefers "Data Not Collected", the policy sentence "Zero does not collect your data" stays as written and the label matches it. Either way, 5.1.2(i) still requires the in-app disclosure and permission described under review risks.

**What Zero Cloud would change.** Everything becomes collected and linked: Contact Info (email), User Content, Audio Data, Identifiers, Financial Info (bank data through SimpleFIN Bridge), Purchases and Diagnostics. The label, the policy and the Terms must change together before launch.

- [ ] Add `PrivacyInfo.xcprivacy` to the app. The app reads and writes `UserDefaults` (`server`, `feedCursor`, `callVoice`, `drafts`), which is a required-reason API category (`NSPrivacyAccessedAPICategoryUserDefaults`, reason `CA92.1`). Declare no tracking and the collected types chosen above. Not verified against Apple's manifest page this session, which returned no content; check before upload.
- [ ] Check that the WebRTC package ships its own privacy manifest and signature, since third-party SDKs must declare theirs.

## Does Each Permission String Match the Code?

| Key | Current text | What the code does | Verdict |
|---|---|---|---|
| `NSMicrophoneUsageDescription` | "Zero listens during calls so you can talk to your team hands free." | Asked at call start (`CallEngine`) and for dictation (`ChatView`) | Reword. It leaves out dictation, says "your team" instead of what Zero is, and does not say the audio leaves the phone. Suggested: "Zero uses the microphone when you call a Zip and when you dictate a message. During calls, your voice is sent to the voice service of your plan." |
| `NSSpeechRecognitionUsageDescription` | "Your words are turned into text when you dictate a message to Zero." | `SFSpeechRecognizer`, forced on-device when the device supports it (`requiresOnDeviceRecognition`), locale en-US | Accurate. Add that recognition may use Apple if the device cannot do it on-device, because that path sends audio to Apple. Locale is fixed to en-US, which is a functional gap for other languages. |
| `NSLocalNetworkUsageDescription` | "Zero uses your network to connect calls." | WebRTC reaches local addresses. The design adds Bonjour discovery of the person's Mac | Reword once Bonjour ships: "Zero looks for your computer on your Wi-Fi so it can connect without the internet." Add `NSBonjourServices` for the service type then. |
| `UIBackgroundModes: audio` | n/a | Keeps a call alive with the screen off | Valid use under 2.5.4 ("VoIP, audio playback"). Must not be used for silent keep-alive. Remove it from the build if calls stop using background audio. |
| `NSUserActivityTypes: INStartCallIntent` | n/a | Recents and Siri re-dial a Zip (`onContinueUserActivity`) | Fine. The code never calls Siri authorization, so no `NSSiriUsageDescription` is needed. If that changes, add one. |
| Notifications | Runtime prompt, no plist string | `Push.register()` asks as soon as the device is paired; onboarding also calls `requestPermission()` | Ask only where the reason is clear (onboarding screen 7). Notifications must not be required for the app to work (4.5.4). |
| Camera, Photos, Contacts, Calendar, Location | none | None of these APIs is used in the iPhone target | Correct to have no strings. Do not add the Mac look-around to the iPhone target. |

Mac-only prompts (Accessibility, Screen Recording, Automation) come from `TaffyMacBridge`. They have no plist strings and live in the outside-the-store Mac app.

## What Are the Review Risks?

Each risk names the rule, what the code does, and what to do.

**1. The app does nothing without a computer.** Apple wants "full access to your app" and, for apps with accounts, "either an active demo account or fully-featured demo mode", with back-end services live (2.1(a) and the submission checklist). A demo mode is allowed instead of a demo account only "with prior approval by Apple". `fixtureMode` exists but works only in simulator test builds (`-fixture`). Missing: a release-build demo mode with canned Zips and chats, a request to Apple for approval, and review notes that explain Zero in a paragraph. Also provide a screen recording of a real call and pairing.

**2. Sending personal data to third-party AI.** Guideline 5.1.2(i): "You must clearly disclose where personal data will be shared with third parties, including with third-party AI, and obtain explicit permission before doing so." The Why We Ask screen says content goes "to Claude or ChatGPT to think" but offers one Continue button and no named permission step. Missing: a consent step that names Anthropic or OpenAI (and Google for the free voice) before the first message or call, with a way to withdraw (5.1.1(ii)). Settings already says "What you say on calls is sent to ChatGPT."

**3. Wording on the Why We Ask screen.** It says "We don't even have servers." Colin's copy rules say never to explain plumbing to the consumer. Reword to "Nothing I learn about you is sent to anyone but the AI company of your own plan."

**4. Background audio (2.5.4).** Audio is a listed background service. Calls play real audio, so it is acceptable. Do not play silence to stay alive, and test that the call ends cleanly on lock, on interruption and on route change (the call logs record these).

**5. CallKit for an AI call.** CallKit makes a Zip call appear in the lock screen, Dynamic Island, Recents and Siri (`CallKitBridge`). Guideline 2.5.12 covers blocking and spam identification and does not ban other uses, and Apple's own CallKit docs are not quoted here (not verified). Reviewer concerns to prepare for: Recents entries (`includesCallsInRecents = true`) for a non-telephone call, a call that is not with a person, and the handle type `.generic`. Mitigation: label the call screen as an AI assistant, keep the Recents entry named after the Zip, and describe the feature in review notes. CallKit availability varies by region and the China mainland storefront has restricted it; check current rules or exclude that storefront (not verified).

**6. Microphone and speech recognition (5.1.1(ii), 5.1.2(i)).** Strings need the rewording in the permission table. Recording and transcript retention must match the policy: transcripts stay on the person's computer for 30 days, with an off switch in Settings (built).

**7. Local network.** The prompt appears when WebRTC or Bonjour touches the LAN. The string must say why. App Transport Security has no exceptions set, so a plain HTTP or self-signed LAN connection to a home computer will be refused. The design pins a certificate at pairing; that needs either a custom trust evaluation or `NSAllowsLocalNetworking`, and the reviewer must be able to see it work.

**8. Notifications (4.5.4).** Notifications must not be required, must not carry sensitive personal information and must not be used for promotion without opt-in. The proactivity design is already careful: Zero's name and under 60 characters, no amounts for money, three a day at most, quiet hours 11 PM to 7 AM, off by default. Keep that. Do not use notifications for upgrade offers.

**9. Account deletion (5.1.1(v)).** The rule applies if the app "supports account creation". Pairing with a code is not account creation, so it does not apply to the free app. Even so, ship a clear way out: Settings has Disconnect This iPhone and a confirmation. Missing: one action that removes the Keychain items, saved chats and the Google voice key (iOS can keep Keychain items after the app is deleted). Zero Cloud would create accounts and require in-app deletion.

**10. Login services (4.8).** If the iPhone app offered Sign in with Google, Claude or ChatGPT as a way into Zero, Sign in with Apple or an equivalent would be required. Today those sign-ins happen on the Mac or on claude.ai, not as a login to Zero. Keep it that way.

**11. Terms of the AI companies (5.2.2).** "If your app uses, accesses, monetizes access to, or displays content from a third-party service, ensure that you are specifically permitted to do so under the service's terms of use. Authorization must be provided upon request." Apple can ask for proof that running Claude Code and Codex on a person's plan is allowed. This is the largest unresolved risk and the reason for `terms-questions.md`.

**12. The screen view looks like a remote desktop (4.2.7).** `ScreenView` shows a Zip's own computer in a web view and lets the person take over for a login. Rule 4.2.7 allows remote-desktop style apps only if "the host device and client must be connected on a local and LAN-based network", and 4.2.7(e) says "Thin clients for cloud-based apps are not appropriate for the App Store". Away-from-home use and any Zero Cloud desktop would both break it. Options: hide the screen view in the store build, limit it to a local network, or describe it in the notes as viewing a browser the person's own Zip uses. A Mac-home design where Zero's window is a browser profile on the person's Mac fits the user-owned-host reading better than Zero Cloud does.

**13. Code that runs on a device (2.5.2).** Apps "may not download, install, or execute code which introduces or changes features or functionality of the app". Zips run commands on the person's computer, not inside the app, and the app never loads new code. Say so in the notes.

**14. Chatbot software (4.7).** The rule lets apps offer chatbots and plug-ins that are not in the binary, and then requires an index of that software, no sharing of permissions without consent, and an age-restriction mechanism (4.7.1 to 4.7.5). Zips are made from templates that Zero ships, but if Zero ever lets people add Zips from outside, 4.7 applies in full.

**15. Payment (3.1.1, 3.1.3(f)).** The free app has none. Unresolved: a paid Mac license sold outside the store, and a Zero Cloud subscription. Apps may not unlock features "with license keys" (3.1.1). A free companion to a paid service can skip in-app purchase only if "there is no purchasing inside the app, or calls to action for purchase outside of the app" (3.1.3(f)). Decision for Colin: either sell Zero Cloud through in-app purchase, or keep every mention of it out of the iPhone app.

**16. Banking (5.1.1(ix)).** Apps in banking and financial services "should be submitted by a legal entity". The free app only shows what the Money Zip gathers from tools the person sets up. Zero Cloud with SimpleFIN Bridge would make this more serious.

**17. App quality (4.2, 4.3(b)).** The June 2026 text warns against apps that are "indistinguishable from what's already widely available". Zero is a client for an agent team on the person's own computer, which is distinct. Lead the description with that.

**18. Name consistency (2.3.1).** Some screens and the Mac bridge still say Taffy ("Bots never work in Taffy itself", "Taffy Mac Bridge"). Clean these up before screenshots.

## Mac Catalyst Specifics

- The Catalyst build is not store-ready (see "Which Builds Go Where?").
- `CallKit` is compiled out of the Catalyst build, so Mac calls use the fallback path. The call logs show `none_mac`.
- Microphone access under the hardened runtime needs `com.apple.security.device.audio-input`, which the file has.
- Keychain items use the `keychain-access-groups` entry with `$(AppIdentifierPrefix)com.example.Taffy`.
- The Mac app asks for Accessibility and Screen Recording through the bridge. Developer ID notarization needs the bridge signed with the hardened runtime and every embedded binary notarized (the design already says so).
- The embedded Python engine in the design adds its own signing and notarization work for the Mac app.

## Ordered Next Steps

1. Ask Anthropic, OpenAI and Google the questions in `terms-questions.md`. Nothing else matters if the answers are no.
2. Lawyer reviews the policy and chooses the App Privacy answers.
3. Build the consent step, the release-build demo mode, the delete-everything action, the privacy manifest and the reworded permission strings.
4. Remove the hard-coded home address and the Taffy names.
5. Create the App Store Connect record, archive with Xcode 26, run internal TestFlight, then external TestFlight with review notes.
6. Submit for App Review with the demo mode and a screen recording.

## Sources

- App Review Guidelines, last updated June 8, 2026: https://developer.apple.com/app-store/review/guidelines/
- Guideline changes of June 8, 2026: https://developer.apple.com/news/?id=a233fmpw
- App privacy details: https://developer.apple.com/app-store/app-privacy-details/
- TestFlight overview: https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview
- Upcoming requirements (SDK minimum April 28, 2026): https://developer.apple.com/news/upcoming-requirements/
- Age ratings: https://developer.apple.com/help/app-store-connect/reference/app-information/age-ratings-values-and-definitions
- Claude Code legal and compliance: https://code.claude.com/docs/en/legal-and-compliance

## Colin's Answers (2026-10-11)

- **No lawyer for now.** There is no budget yet, so the privacy policy and terms are written in-house from `privacy-policy-draft.md`, and the App Privacy answers are chosen from the table above.
- **No company.** Zero ships under Colin's individual developer account, so the store shows his name as the seller.
- **Create the app record and archive.** Approved. The App Store Connect API cannot create app records (it returns "apps does not allow CREATE"), so the record is made by hand on the website. App Store search on Oct 11 showed no live US app named exactly "Zero"; an unreleased app could still hold the name.
