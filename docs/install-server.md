# Run Zero on Your Own Home Server

## Who Is This For?

This guide is for anyone who has a spare computer at home running Linux and wants Zero to live there. The computer stays on, your Zips work on it day and night, and you talk to them from the Zero app on your iPhone. Everything stays on your computer and uses your own Claude or ChatGPT plan.

If you only have a Mac, use Zero for Mac instead. This guide is for a Linux computer.

## What Do You Need?

- A computer running Ubuntu 24.04 or another Debian-like Linux, with an Intel, AMD or ARM processor. It should have at least 8 GB of memory and 20 GB of free disk space.
- A user account on that computer that can use sudo.
- A Claude Pro or Max plan, or a ChatGPT Plus or Pro plan. Your Zips think with Claude, so a Claude plan is the one to have. A ChatGPT plan adds voice calls.
- The Zero app on your iPhone.
- A free Tailscale account if you want to reach Zero when you are away from home. You can skip it and use Zero on your home Wi-Fi only.

## How Do I Install It?

Log in to the computer as yourself, open a terminal, and run this one command:

```
curl -fsSL https://raw.githubusercontent.com/ColinKOB/zero/main/install.sh | bash
```

The installer asks for your sudo password once. Then it asks a few questions:

1. Your name and your time zone.
2. Whether to use Tailscale. If you say yes, it installs Tailscale and shows a link where you sign in with your own Tailscale account.
3. Which AI plan to use. It installs the official Claude Code or Codex command line and signs it in with your own account. Open the link it shows on any device, sign in, and paste the code back if it asks for one. You can skip this step and sign in later.

The first install takes 10 to 20 minutes, most of it spent building the sandbox your Zips work in. When it finishes it shows a QR code.

## How Do I Pair My iPhone?

Point your iPhone's Camera at the QR code in the terminal and tap the link. The Zero app opens with your server's address and a one-time code filled in.

If the camera is not handy, open Zero, choose I Have My Code, and type the address and code the installer printed. The code works once and lasts for one hour. For a new one, run:

```
bash ~/.local/share/zero/install.sh pair
```

If you chose Tailscale, install the Tailscale app on your iPhone and sign in with the same account before you pair. If you skipped Tailscale, your iPhone needs to be on the same Wi-Fi as the computer.

## How Do I Sign In to My AI Plan Later?

Run this on the computer:

```
bash ~/.local/share/zero/install.sh signin
```

You can also choose Sign In With Claude in the app after pairing.

## How Do I Check That It Is Running?

```
bash ~/.local/share/zero/install.sh status
```

Zero starts by itself when the computer starts, even before anyone logs in.

## How Do I Update It?

Run the same install command again. It downloads the newest Zero, keeps your settings, Zips and chats, and restarts what changed. Running it again is also how you repair an install that stopped working.

## How Do I Remove It?

```
bash ~/.local/share/zero/install.sh uninstall
```

This stops Zero and removes its programs. It then asks whether to delete your Zips' memories, chats and files. Press Enter to keep them, or type delete to remove them for good. Tailscale, Claude Code, Codex and your sign-ins stay installed because they are yours.

## What Does It Change on My Computer?

- It installs a few system packages, Docker's rootless tools and, if you chose it, Tailscale.
- It keeps Zero in your own home folder: `~/taffy` for the code and settings, `~/.taffy` for your Zips' memories and chats, and `~/.local/share/zero` for the installer.
- It turns on lingering for your user, so Zero's services keep running after you log out.
- Your Zips work inside containers that belong to your user. They can reach the internet but not your home network.
- Zero listens only on the computer itself. Tailscale publishes it to your own devices. Without Tailscale, it listens on your home network over HTTPS with its own certificate, which the app checks when it pairs, and the installer opens that one port in the firewall if a firewall is on.

## Answering Ahead of Time

Each question can be answered before you start, which is useful for a computer you set up over SSH:

```
curl -fsSL https://raw.githubusercontent.com/ColinKOB/zero/main/install.sh | \
  ZERO_NAME="Sam Rivera" ZERO_TIMEZONE=America/Chicago ZERO_TAILSCALE=yes ZERO_AI=claude bash
```

`ZERO_SANDBOX=local` skips Docker. Your lead Zip then works directly on the computer as you, and adding more Zips needs Docker later. `ZERO_SOURCE` points the installer at another copy of Zero, such as a release archive.
