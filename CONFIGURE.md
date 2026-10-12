# Configure Zero for Your Own Accounts

This copy of Zero ships with placeholders where the original used its author's Apple team and developer apps. Replace each placeholder with your own value before you build the app or run a hub. None of these values are secrets, but they decide whose developer apps your install talks to.

To find every placeholder, search the repository for `YOUR_`, `yourgoogleclientid`, `com.example` and `relay.example.com`.

## Where Do the Hub Settings Live?

Each home has a settings file, named by the `ZERO_HOME` environment variable. A value in that file overrides the built-in default. The defaults live in three places that should stay the same:

- `hub/taffy_hub/home.py` (the hub)
- `alien/plugins/taffy-tools/home.py` (the Hermes plugin)
- `alien/bin/zero-home-lib.py` (the scripts that create a home)

You can either put your values in each home's settings file or change the defaults in all three files.

## Apple

| What | Where to get it | Where it goes |
|---|---|---|
| Team ID (placeholder `YOUR_TEAM_ID`) | [Apple Developer, Membership](https://developer.apple.com/account) | `DEVELOPMENT_TEAM` in `ios/project.yml`; `push.team_id` in the hub settings; `TEAM` in `ios/scripts/release.sh` |
| Bundle ID prefix (placeholder `com.example`) | A reverse domain you control | `bundleIdPrefix` and every `PRODUCT_BUNDLE_IDENTIFIER` in `ios/project.yml`; the app group `group.com.example.Taffy` in `ios/Taffy/Taffy-Communication.entitlements`, `ios/NotificationService/NotificationService.entitlements` and `ios/PushShared/ZipNotify.swift`; the keychain group in `ios/Taffy/Taffy-Mac.entitlements`; `ios/Taffy/Model/Support.swift`, `ios/Taffy/Model/AppState.swift`, `ios/Taffy/Info.plist` and `ios/scripts/install-mac.sh`; `push.topic` in the hub settings |
| Push key | Apple Developer, Keys: create a key with Apple Push Notifications enabled | Save the `.p8` file in the folder named by `push.key_dir` in the hub settings. Never commit it. |
| App Store Connect API key (placeholders `YOUR_ASC_KEY_ID` and an all-zero issuer ID) | App Store Connect, Users and Access, Integrations | `KEY_ID` and `ISSUER` in `ios/scripts/appstore.py` and `ios/scripts/release.sh`. Only needed for release uploads. The `.p8` file stays in `~/.appstoreconnect/private_keys`. |

Run `cd ios && xcodegen generate` after you change `ios/project.yml`.

## Google (Gmail, Google Calendar and Google Drive)

Register an OAuth client of type iOS in the [Google Cloud console](https://console.cloud.google.com/apis/credentials), using your bundle ID. Enable the Gmail, Google Calendar and Google Drive APIs, and set up the OAuth consent screen. Zero asks for read access to mail, permission to send mail, calendar access, and read-only Drive access.

The client ID looks like `123456789012-abc123.apps.googleusercontent.com`. Replace `000000000000-yourgoogleclientid` with the part before `.apps.googleusercontent.com` in these places:

- `GIDClientID` and the `com.googleusercontent.apps.` URL scheme in `ios/project.yml` (and in `ios/Taffy/Info.plist`, which XcodeGen writes)
- `google.client_id` in the hub settings
- `ZERO_GOOGLE_CLIENT_ID` in `alien/bin/zero-home-lib.py` and `ZERO_CLIENT_ID_DEFAULT` in `tools/zero-home-wizard.sh`

## Connected Apps

Each of these is optional. A service whose setting is still a placeholder will not connect. The app's sign-in sheets return to the `zero://oauth/<service>` address, so register that as the redirect where a service asks for one.

| Service | Where to register | Setting in the hub settings | Notes |
|---|---|---|---|
| Trello | [Trello Power-Up admin](https://trello.com/power-ups/admin), then generate an API key | `apps.trello.key` (placeholder `YOUR_TRELLO_API_KEY`) | The hub accepts only a 32-character hex key. Each person allows the app on Trello's own page. |
| GitHub | [GitHub Developer settings, OAuth Apps](https://github.com/settings/developers) | `apps.github.client_id` (placeholder `YOUR_GITHUB_CLIENT_ID`) | Turn on device flow. No client secret is used. |
| Dropbox | [Dropbox App Console](https://www.dropbox.com/developers/apps) | `apps.dropbox.app_key` (placeholder `YOUR_DROPBOX_APP_KEY`) | Use PKCE with redirect `zero://oauth/dropbox` and the `files.metadata.read` and `files.content.read` scopes. |
| Linear | Linear, Settings, API, OAuth applications | `apps.linear.client_id` (placeholder `YOUR_LINEAR_CLIENT_ID`) | Public client with PKCE and redirect `zero://oauth/linear`. |
| Microsoft (Outlook) | [Microsoft Entra admin center, App registrations](https://entra.microsoft.com) | `apps.microsoft.client_id` (placeholder `YOUR_MICROSOFT_CLIENT_ID`) | Allow work, school and personal accounts, as a public client with redirect `zero://oauth/microsoft`. |
| Notion | [Notion integrations](https://www.notion.so/profile/integrations), as a public integration | `apps.notion.client_id` (placeholder `YOUR_NOTION_CLIENT_ID`) | Notion needs a client secret, so it signs in through the OAuth relay below. Its redirect is `<relay>/callback/notion`. |

## OAuth Relay

Some services, such as Notion, give no sign-in without a client secret, and a secret cannot ship inside the app or every hub. The service in `oauth-relay/` holds those secrets and swaps codes for tokens. It keeps no data.

Deploy `oauth-relay/` on any host that gives it a public HTTPS address. Set `<SERVICE>_CLIENT_ID` and `<SERVICE>_CLIENT_SECRET` as environment variables there, for example `NOTION_CLIENT_ID` and `NOTION_CLIENT_SECRET`. Then put its address in `apps.relay.url` in the hub settings, replacing `https://relay.example.com`.

## Voice

Voice calls use a free Google AI Studio key that each person gets for themselves through the app's Give Zero a Voice steps. There is nothing to configure here.

## Other Settings Worth Checking

- `integrations.whisper_url` points at a speech-to-text server on the author's home network. Set it to your own Whisper server or to `null`.
- `network.public_url` is the HTTPS address the app uses to reach your hub, such as your Tailscale Serve address.
- `ios/scripts/install-iphone.sh` has a `DEFAULT_PHONE` of all zeros. Set it to your iPhone's CoreDevice ID, or pass `--device "<your phone's name>"` each time.
