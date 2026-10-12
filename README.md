# Zero

Zero is a personal assistant you host yourself. You talk to it from an iPhone or Mac app, by text or by voice call. Behind the app is a small team of AI helpers called Zips. Each Zip has its own job, its own memory and its own sandboxed computer, and they hand work to each other on a shared board.

The Zips run on a server you own, called the hub. They use your own Claude or ChatGPT plan, so Zero has no servers or accounts of its own. Anything that changes the world, such as sending an email or spending money, comes to you as a card in the app first.

## What Is in This Repository?

- `ios/` holds the iPhone and Mac app (SwiftUI, with the Mac app built through Mac Catalyst).
- `hub/` holds the hub, a FastAPI service the app talks to. It runs next to the Hermes agent gateway.
- `alien/` holds the server side: the Hermes plugin with Zero's tools, the Zip sandbox, systemd units, and the scripts that set up a home on a Linux server. The folder is named after the author's server.
- `oauth-relay/` holds a small optional service that finishes sign-in for apps that require a client secret.
- `mac/` holds an optional job that copies the server's backups to a Mac.
- `docs/` holds design notes and specs.

## How Do I Build the iPhone and Mac App?

You need a Mac with a current version of Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen).

1. Fill in your Apple team ID and bundle ID prefix as described in [CONFIGURE.md](CONFIGURE.md).
2. Generate the project with `cd ios && xcodegen generate`.
3. Open `ios/Taffy.xcodeproj` and run the `Taffy` scheme on your iPhone, or on "My Mac (Mac Catalyst)" for the Mac app.

Two scripts do the same from the command line. `ios/scripts/install-iphone.sh --device "<your phone's name>"` builds and installs the app on one connected iPhone. `ios/scripts/install-mac.sh` builds the Mac app and installs it in /Applications.

## How Do I Run a Hub?

On a Linux computer at home (Ubuntu 24.04 or another Debian-like system), one command installs everything and prints a pairing code for the app. See [docs/install-server.md](docs/install-server.md).

```
curl -fsSL https://raw.githubusercontent.com/ColinKOB/zero/main/install.sh | bash
```

The hub needs a Linux or macOS machine with Python 3.12 or later and an installed copy of [Hermes Agent](https://github.com/NousResearch/hermes-agent). The Zips also need the Claude Code or Codex command line tool signed in to your own plan.

For a local trial:

1. Create a virtual environment and install the hub with `python3 -m venv .venv && .venv/bin/pip install -e hub`.
2. Copy `hub/tests/homes/stranger.json` to a file of your own and edit the person, paths and accounts. This file is your home's settings. For a single home, set `plan.claude` to `{"mode": "own", "command": "claude"}` so the Zips use your own Claude Code sign-in.
3. Start the hub with `ZERO_HOME=/path/to/home.json HERMES_HOME=~/.taffy .venv/bin/python -m uvicorn taffy_hub.app:app --app-dir hub --port 8650`.
4. Run `ZERO_HOME=/path/to/home.json .venv/bin/taffy-hub pair` to get a pairing code and link, and open the link or enter the code in the app.

For an always-on server, the scripts in `alien/bin` set up each home as its own Linux user with its own services. Start with `alien/bin/zero-home-sudo-prep` and `alien/bin/zero-home-create`; each script explains its steps at the top and supports `--dry-run`. They were written for the author's server, so read them before you run them.

The phone reaches the hub over [Tailscale](https://tailscale.com). The hub listens only on the local machine, and Tailscale Serve gives it an HTTPS address on your tailnet. Put that address in your home's settings as `network.public_url`.

## How Do I Run the Tests?

Run `cd hub && python3 -m unittest discover -s tests` for the hub, and `cd alien/broker && python3 -m unittest discover -s tests` for the server scripts. A few tests read files that are not in this repository and fail without them.

## License

Zero is released under the MIT License. See [LICENSE](LICENSE). Bundled third-party code keeps its own license, as listed at the end of that file.
