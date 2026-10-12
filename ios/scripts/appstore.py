#!/usr/bin/env python3
"""App Store Connect steps for release.sh, through the App Store Connect API (no Apple ID sign-in or 2FA).

  appstore.py profiles ios|mac        makes and installs the App Store (iPhone) or Developer ID (Mac) profiles and
                                       prints them as export-options plist lines
  appstore.py app-id                 prints the app's App Store Connect id, or exits 3 if there is no app record
  appstore.py publish <build>        waits for the uploaded build to finish processing, then sets up and fills the
                                       "Public Preview" external group, adds the build, submits it for Beta App
                                       Review and prints the public link

Every step checks what is already there first, so running it again is safe. Needs PyJWT (pip install pyjwt).
The API key's .p8 file stays in ~/.appstoreconnect/private_keys; the key id and issuer id are not secret.
"""
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt

KEY_ID = "YOUR_ASC_KEY_ID"
ISSUER = "00000000-0000-0000-0000-000000000001"
KEY = pathlib.Path.home() / ".appstoreconnect/private_keys" / f"AuthKey_{KEY_ID}.p8"
BUNDLE_ID = "com.example.Taffy"
API = "https://api.appstoreconnect.apple.com"

GROUP = "Public Preview"
TESTER_LIMIT = 1000
FEEDBACK_EMAIL = "admin@example.com"
DESCRIPTION = ("Zero is a team of AI coworkers called Zips that live on your own computer or our server and help "
               "with school, work, money and more.")
WHAT_TO_TEST = ("Set up Zero, meet your first Zip and ask it for help with something real. Try a call with a Zip. "
                "If you have no computer to set up, tap Explore a Sample Home on the first screen. Please tell us "
                "about anything that is confusing, slow or broken.")
REVIEW_NOTES = ("Zero connects to a home that the person runs on their own computer, so a reviewer has no home to "
                "pair with. Tap Explore a Sample Home on the first screen to see chat, cards, Zips and settings with "
                "sample data. No account or sign-in is needed.")
CONTACT_FIELDS = ("contactFirstName", "contactLastName", "contactPhone", "contactEmail")


def token():
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "iat": now, "exp": now + 900, "aud": "appstoreconnect-v1"},
                      KEY.read_text(), algorithm="ES256", headers={"kid": KEY_ID, "typ": "JWT"})


def call(method, path, body=None, ok=(200, 201, 204)):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        data = json.loads(raw) if raw else {}
        if e.code in ok:
            return e.code, data
        return e.code, data


def must(method, path, body=None):
    status, data = call(method, path, body)
    if status >= 300:
        details = "; ".join(f"{x.get('title')}: {x.get('detail')}" for x in data.get("errors", []))
        sys.exit(f"App Store Connect said {status} to {method} {path.split('?')[0]}: {details}")
    return data


def app_id():
    data = must("GET", f"/v1/apps?filter%5BbundleId%5D={BUNDLE_ID}")["data"]
    return data[0]["id"] if data else None


# Distribution profiles, made here because Xcode's automatic export asks for cloud signing, which this API key may not
# use. Each is (bundle id, profile type, certificate type, profile name). Profiles pick up the App ID's capabilities
# when they are made, so a profile that went invalid after a capability change is replaced with a fresh one.
PROFILES = {
    "ios": [("com.example.Taffy", "IOS_APP_STORE", "DISTRIBUTION", "Zero App Store"),
            ("com.example.Taffy.NotificationService", "IOS_APP_STORE", "DISTRIBUTION",
             "Zero Notifications App Store")],
    "mac": [("com.example.Taffy", "MAC_CATALYST_APP_DIRECT", "DEVELOPER_ID_APPLICATION_G2",
             "Zero Mac Developer ID")],
}


def local_serials():
    """Serial numbers of the signing certificates that have a private key in this Mac's keychain."""
    import re
    import subprocess
    out = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                         capture_output=True, text=True).stdout
    serials = set()
    for name in set(re.findall(r'"([^"]+)"', out)):
        pems = subprocess.run(["security", "find-certificate", "-a", "-c", name, "-p"],
                              capture_output=True, text=True).stdout
        for pem in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pems, re.S):
            s = subprocess.run(["openssl", "x509", "-noout", "-serial"], input=pem,
                               capture_output=True, text=True).stdout
            serials.add(s.strip().split("=")[-1].upper())
    return serials


def profiles(kind):
    """Makes sure each profile exists and is active, installs it, and prints plist lines mapping bundle id to UUID."""
    import base64
    import datetime
    import plistlib
    mine = local_serials()
    certs = must("GET", "/v1/certificates?limit=200")["data"]
    store = pathlib.Path.home() / "Library/Developer/Xcode/UserData/Provisioning Profiles"
    store.mkdir(parents=True, exist_ok=True)
    lines = []
    for bundle, ptype, ctype, name in PROFILES[kind]:
        cert = next((c for c in certs if c["attributes"]["certificateType"] == ctype
                     and c["attributes"]["serialNumber"].upper() in mine), None)
        if cert is None:
            sys.exit(f"No {ctype} certificate with its private key in this Mac's keychain.")
        bid = must("GET", f"/v1/bundleIds?filter%5Bidentifier%5D={bundle}")["data"]
        bid = next((b for b in bid if b["attributes"]["identifier"] == bundle), None)
        if bid is None:
            sys.exit(f"The App ID {bundle} is not registered.")
        found = must("GET", f"/v1/profiles?filter%5Bname%5D={urllib.parse.quote(name)}"
                            f"&filter%5BprofileType%5D={ptype}&include=certificates&limit=20")
        prof = None
        for p in found["data"]:
            certs_in = [c["id"] for c in p["relationships"]["certificates"].get("data", [])]
            if p["attributes"]["profileState"] == "ACTIVE" and cert["id"] in certs_in:
                prof = p
                break
        if prof is None:
            label = name if not found["data"] else f"{name} {datetime.datetime.now():%Y-%m-%d %H%M%S}"
            prof = must("POST", "/v1/profiles",
                        {"data": {"type": "profiles", "attributes": {"name": label, "profileType": ptype},
                                  "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bid["id"]}},
                                                    "certificates": {"data": [{"type": "certificates",
                                                                               "id": cert["id"]}]}}}})["data"]
        content = base64.b64decode(prof["attributes"]["profileContent"])
        uuid = prof["attributes"]["uuid"]
        ext = ".provisionprofile" if kind == "mac" else ".mobileprovision"
        (store / f"{uuid}{ext}").write_bytes(content)
        lines.append(f"<key>{bundle}</key><string>{uuid}</string>")
    print("\n".join(lines))


def wait_for_build(app, version, minutes=60):
    """The upload shows up in the API a few minutes after altool finishes, then processes for a while longer."""
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        data = must("GET", f"/v1/builds?filter%5Bapp%5D={app}&filter%5Bversion%5D={version}&limit=1")["data"]
        if data:
            state = data[0]["attributes"]["processingState"]
            if state == "VALID":
                return data[0]
            if state in ("FAILED", "INVALID"):
                sys.exit(f"Build {version} failed processing at Apple ({state}). Check the email Apple sent.")
        print(f"  waiting for Apple to process build {version}...", flush=True)
        time.sleep(30)
    sys.exit(f"Build {version} did not finish processing within {minutes} minutes. Run: "
             f"ios/scripts/appstore.py publish {version}")


def ensure_test_info(app):
    locs = must("GET", f"/v1/apps/{app}/betaAppLocalizations")["data"]
    attrs = {"feedbackEmail": FEEDBACK_EMAIL, "description": DESCRIPTION}
    mine = next((l for l in locs if l["attributes"]["locale"] == "en-US"), None)
    if mine:
        must("PATCH", f"/v1/betaAppLocalizations/{mine['id']}",
             {"data": {"type": "betaAppLocalizations", "id": mine["id"], "attributes": attrs}})
    else:
        must("POST", "/v1/betaAppLocalizations",
             {"data": {"type": "betaAppLocalizations", "attributes": {"locale": "en-US", **attrs},
                       "relationships": {"app": {"data": {"type": "apps", "id": app}}}}})
    detail = must("GET", f"/v1/apps/{app}/betaAppReviewDetail")["data"]
    must("PATCH", f"/v1/betaAppReviewDetails/{detail['id']}",
         {"data": {"type": "betaAppReviewDetails", "id": detail["id"],
                   "attributes": {**review_contact(app, detail), "demoAccountRequired": False,
                                  "notes": REVIEW_NOTES}}})


def review_contact(app, detail):
    """Beta App Review needs a contact. Keep the app's own, or copy the one Colin's other apps already give Apple,
    so no personal details live in this repo."""
    if all(detail["attributes"].get(k) for k in CONTACT_FIELDS):
        return {k: detail["attributes"][k] for k in CONTACT_FIELDS}
    for other in must("GET", "/v1/apps?limit=50")["data"]:
        if other["id"] == app:
            continue
        _, d = call("GET", f"/v1/apps/{other['id']}/betaAppReviewDetail")
        attrs = (d.get("data") or {}).get("attributes", {})
        if all(attrs.get(k) for k in CONTACT_FIELDS):
            return {k: attrs[k] for k in CONTACT_FIELDS}
    sys.exit("No review contact on file. Fill in Beta App Review Information in App Store Connect, TestFlight, "
             "Test Information, then run this again.")


def ensure_what_to_test(build):
    locs = must("GET", f"/v1/builds/{build}/betaBuildLocalizations")["data"]
    mine = next((l for l in locs if l["attributes"]["locale"] == "en-US"), None)
    if mine:
        must("PATCH", f"/v1/betaBuildLocalizations/{mine['id']}",
             {"data": {"type": "betaBuildLocalizations", "id": mine["id"], "attributes": {"whatsNew": WHAT_TO_TEST}}})
    else:
        must("POST", "/v1/betaBuildLocalizations",
             {"data": {"type": "betaBuildLocalizations", "attributes": {"locale": "en-US", "whatsNew": WHAT_TO_TEST},
                       "relationships": {"build": {"data": {"type": "builds", "id": build}}}}})


def ensure_group(app):
    groups = must("GET", f"/v1/apps/{app}/betaGroups?limit=200")["data"]
    group = next((g for g in groups if g["attributes"]["name"] == GROUP), None)
    attrs = {"publicLinkEnabled": True, "publicLinkLimitEnabled": True, "publicLinkLimit": TESTER_LIMIT,
             "feedbackEnabled": True}
    if group is None:
        group = must("POST", "/v1/betaGroups",
                     {"data": {"type": "betaGroups", "attributes": {"name": GROUP, **attrs},
                               "relationships": {"app": {"data": {"type": "apps", "id": app}}}}})["data"]
    elif not group["attributes"].get("publicLinkEnabled"):
        group = must("PATCH", f"/v1/betaGroups/{group['id']}",
                     {"data": {"type": "betaGroups", "id": group["id"], "attributes": attrs}})["data"]
    return group


def publish(version):
    app = app_id()
    if not app:
        sys.exit(f"No App Store Connect app record for {BUNDLE_ID}. Create it first (see release.sh).")
    build = wait_for_build(app, version)
    bid = build["id"]
    # Export compliance: the app uses only standard HTTPS encryption, which is exempt.
    if build["attributes"].get("usesNonExemptEncryption") is None:
        must("PATCH", f"/v1/builds/{bid}",
             {"data": {"type": "builds", "id": bid, "attributes": {"usesNonExemptEncryption": False}}})
    ensure_test_info(app)
    ensure_what_to_test(bid)
    group = ensure_group(app)
    must("POST", f"/v1/betaGroups/{group['id']}/relationships/builds", {"data": [{"type": "builds", "id": bid}]})
    status, data = call("POST", "/v1/betaAppReviewSubmissions",
                        {"data": {"type": "betaAppReviewSubmissions",
                                  "relationships": {"build": {"data": {"type": "builds", "id": bid}}}}})
    if status >= 300:
        details = "; ".join(f"{x.get('detail')}" for x in data.get("errors", []))
        print(f"Beta App Review submission was not accepted ({status}): {details}")
    _, sub = call("GET", f"/v1/builds/{bid}/betaAppReviewSubmission")
    state = (sub.get("data") or {}).get("attributes", {}).get("betaReviewState", "not submitted")
    group = must("GET", f"/v1/betaGroups/{group['id']}")["data"]
    print(f"Build {version}: Beta App Review state {state}.")
    print(f"Public link ({GROUP}): {group['attributes'].get('publicLink') or 'not issued yet'}")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "app-id":
        found = app_id()
        if not found:
            sys.exit(3)
        print(found)
    elif len(sys.argv) == 3 and sys.argv[1] == "profiles" and sys.argv[2] in PROFILES:
        profiles(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "publish":
        publish(sys.argv[2])
    else:
        sys.exit(__doc__)
