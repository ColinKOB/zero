"""Custom Rules (from Grok Bot 0.68.1; docs/qa/2026-10-09-custom-rules.md): Ask First rules the gate honors, Allow
rules that are never widened, Edit Rule, "Allowed from now on" after Always Allow, and "No answer in time" when a
card runs out of time.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, gate, plugin, tt  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402
from taffy_hub import store, team  # noqa: E402

tiers = tt.tiers
HIMALAYA = ("himalaya message send -a personal <<'EOF'\nTo: ken@brightpath.test\nSubject: Repo access\n\n"
            "Hi Theo, still waiting on the setup details.\nEOF")


class AskFirstInTheGate(GateCase):
    """An Ask First rule turns something the Zip would just do into a card, and only ever adds a card."""

    bot = "builder"

    def push(self, branch="fix-flaky-test"):
        return self.call("terminal", {"command": f"git push origin {branch}"})

    def action_cards(self):
        return [c for c in self.cards() if c["kind"] not in ("signin", "navigate", None)]

    def test_a_branch_push_is_told_until_colin_asks_to_be_asked(self):
        self.assertIsNone(self.push())
        rid = plugin.set_ask_rule("builder", "push")
        blocked = self.push("second-branch")
        self.assertEqual(blocked["action"], "block")
        self.assertIn("Colin set a rule that you ask him before pushing code to any repo", blocked["message"])
        card = self.action_cards()[0]
        self.assertEqual((card["kind"], card["ask_rule"], card["scope"]), ("write", rid, ""))
        self.assertEqual(plugin.get_rule(rid)["uses"], 1)
        plugin.decide(card["id"], True)
        self.assertIsNone(self.push("second-branch"))
        self.assertIsNotNone(self.push("second-branch"))
        plugin.remove_rule(rid)
        self.assertIsNone(self.push("third-branch"))

    def test_a_rule_for_another_zip_does_not_apply_but_every_zip_does(self):
        plugin.set_ask_rule("school", "push")
        self.assertIsNone(self.push())
        plugin.set_ask_rule(plugin.EVERY_ZIP, "push")
        self.assertIsNotNone(self.push("other"))

    def test_a_push_rule_for_one_branch_covers_only_that_branch(self):
        plugin.set_ask_rule("builder", "push", "staging")
        self.assertIsNone(self.push("fix-flaky-test"))
        self.assertIsNotNone(self.push("staging"))

    def test_a_change_rule_in_google_docs_covers_drive_but_not_trello(self):
        self.ask("tidy up my school docs and my board")
        plugin.set_ask_rule("builder", "change", "google-docs")
        drive = {"service": "drive", "request": "Rename the essay draft in my Drive to Final essay"}
        self.assertEqual(tiers.classify("connectors", drive).kind, "write")
        self.assertIsNotNone(self.call("connectors", drive))
        self.assertEqual(self.action_cards()[0]["site"], "drive.google.com")
        self.assertIsNone(self.call("connectors", {"service": "trello", "request": "Move the Engel summary card to Done"}))

    def test_a_send_rule_overrides_an_allow_rule_and_offers_no_new_one(self):
        plugin.set_rule("builder", "send", "ken@brightpath.test", source="card 1")
        self.assertIsNone(self.call("terminal", {"command": HIMALAYA}))
        plugin.set_ask_rule("builder", "send", "ken@brightpath.test")
        blocked = self.call("terminal", {"command": HIMALAYA.replace("still waiting", "checking in")})
        self.assertIsNotNone(blocked)
        card = self.action_cards()[0]
        self.assertEqual(card["scope"], "")
        self.assertIsNotNone(card["ask_rule"])

    def test_an_ask_rule_never_unlocks_never(self):
        plugin.set_ask_rule(plugin.EVERY_ZIP, "change")
        blocked = self.call("terminal", {"command": "stripe refunds create --charge ch_1"})
        self.assertTrue(blocked["message"].startswith("Not done"))
        self.assertEqual(self.cards(), [])

    def test_publishing_still_asks_with_or_without_a_rule(self):
        self.assertIsNotNone(self.push("main"))
        plugin.set_ask_rule("builder", "push")
        self.assertIsNotNone(self.push("main"))


class RuleStore(GateCase):
    def test_ask_rules_are_never_read_as_allow_rules(self):
        plugin.set_ask_rule("money", "send", "andrea@x.com")
        self.assertIsNone(plugin.rule_for("money", "send", "andrea@x.com"))
        self.assertIsNone(plugin.rule_for("money", "ask:send", "andrea@x.com"))
        with self.assertRaises(ValueError):
            plugin.set_rule("money", "ask:send", "andrea@x.com")
        for kind in ("pay", "delete", "publish", "account", "secret"):
            with self.assertRaises(ValueError):
                plugin.set_rule("money", kind, "anything")

    def test_where_is_checked(self):
        w = plugin.ask_rule_where
        self.assertEqual(w("push", ""), "*")
        self.assertEqual(w("push", "refs/heads/Main"), "main")
        self.assertEqual(w("change", "https://docs.google.com/document/d/1"), "docs.google.com")
        self.assertEqual(w("change", "google-docs"), "google-docs")
        self.assertEqual(w("send", "Andrea@X.com"), "andrea@x.com")
        self.assertEqual(w("send", "@brightpath.test"), "@brightpath.test")
        for what, where in [("push", "a b c"), ("change", "andrea@x.com"), ("change", "not a site"), ("pay", "*")]:
            with self.subTest(what=what, where=where), self.assertRaises(ValueError):
                w(what, where)

    def test_phrases(self):
        p = plugin.ask_rule_phrase
        self.assertEqual(p("push", "*"), "pushing code to any repo")
        self.assertEqual(p("change", "google-docs"), "changing anything in Google Docs and Drive")
        self.assertEqual(p("send", "andrea@x.com"), "sending anything to andrea@x.com")
        self.assertEqual(p("send", "@x.com"), "sending anything to anyone at x.com")

    def test_an_allow_rule_moves_to_another_zip_but_is_never_widened(self):
        rid = plugin.set_rule("money", "send", "andrea@x.com", source="card 3")
        self.assertEqual(plugin.update_rule(rid, bot_id="clients")["bot_id"], "clients")
        for change in [{"where": "@x.com"}, {"where": "*"}, {"what": "write"}, {"bot_id": "*"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                plugin.update_rule(rid, **change)
        r = plugin.get_rule(rid)
        self.assertEqual((r["kind"], r["scope"]), ("send", "andrea@x.com"))
        with self.assertRaises(LookupError):
            plugin.update_rule(99999, bot_id="money")

    def test_an_ask_rule_can_change_what_and_where_and_merges_with_a_twin(self):
        rid = plugin.set_ask_rule("builder", "change", "*")
        r = plugin.update_rule(rid, what="push", where="staging")
        self.assertEqual((r["kind"], r["scope"]), ("ask:push", "staging"))
        twin = plugin.set_ask_rule("school", "push", "staging")
        merged = plugin.update_rule(rid, bot_id="school")
        self.assertEqual(merged["id"], twin)
        self.assertIsNone(plugin.get_rule(rid))


class CustomRulesInTheApp(AppCase):
    def rules(self, **params):
        return self.client.get("/api/rules", params=params).json()["rules"]

    def test_rules_read_as_plain_sentences(self):
        plugin.set_rule("money", "send", "andrea@x.com", source="card 1")
        plugin.set_ask_rule("builder", "push")
        plugin.set_ask_rule("school", "change", "google-docs")
        plugin.set_ask_rule(plugin.EVERY_ZIP, "send", "@x.com")
        got = {r["sentence"] for r in self.rules()}
        self.assertEqual(got, {"Money can email andrea@x.com without asking.",
                               "Builder asks before pushing code to any repo.",
                               "School asks before changing anything in Google Docs and Drive.",
                               "Every Zip asks before sending anything to anyone at x.com."})
        school = self.rules(bot_id="school")
        self.assertEqual({r["bot_id"] for r in school}, {"school", "*"})
        self.assertEqual([r["kind"] for r in self.client.get("/api/permissions").json()["rules"]], ["send"])
        choices = self.client.get("/api/rules").json()["choices"]
        self.assertIn({"id": "push", "title": "Pushing code to any repo"}, choices["what"])
        self.assertEqual(choices["places"][0], {"id": "*", "title": "Anywhere"})

    def test_add_and_edit_say_saved(self):
        r = self.client.post("/api/rules", json={"bot_id": "builder", "what": "push", "where": "*"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["message"], "Saved to Custom Rules")
        rid = r.json()["rule"]["id"]
        r = self.client.put(f"/api/rules/{rid}", json={"bot_id": "school", "what": "change", "where": "google-docs"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["message"], r.json()["rule"]["sentence"]),
                         ("Saved to Custom Rules", "School asks before changing anything in Google Docs and Drive."))
        self.assertEqual(self.client.post("/api/rules", json={"bot_id": "builder", "what": "pay"}).status_code, 400)
        self.assertEqual(self.client.post("/api/rules", json={"bot_id": "nobody", "what": "push"}).status_code, 404)
        self.assertEqual(self.client.put("/api/rules/9999", json={"bot_id": "school"}).status_code, 404)

    def test_an_allow_rule_edit_cannot_widen_it(self):
        rid = plugin.set_rule("money", "send", "andrea@x.com", source="card 1")
        r = self.client.put(f"/api/rules/{rid}", json={"where": "*"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("cannot be narrowed further", r.json()["detail"])
        self.assertEqual(self.client.put(f"/api/rules/{rid}", json={"bot_id": "*"}).status_code, 404)
        r = self.client.put(f"/api/rules/{rid}", json={"bot_id": "clients"})
        self.assertEqual(r.json()["rule"]["sentence"], "Clients can email andrea@x.com without asking.")

    def test_a_paused_card_names_the_rule(self):
        rid = plugin.set_ask_rule("builder", "push")
        plugin.action_request("builder", "write", "", "Push to the branch fix", "fix", "", "fp-push", "chat-1", None,
                              why="asked", ask_rule=rid)
        card = self.client.get("/api/signin-requests").json()["requests"][0]
        self.assertEqual(card["paused_by"], "Builder asks before pushing code to any repo.")
        self.assertFalse(card["can_always"])

    def test_always_allow_confirms_the_rule_in_plain_words(self):
        req = plugin.action_request("builder", "send", "", "Email ken@brightpath.test", "ken@brightpath.test",
                                    "Hi Theo", "fp1", "chat-1", None, why="asked", scope="ken@brightpath.test")
        r = self.client.post(f"/api/signin-requests/{req}", json={"decision": "always"}).json()
        line = "Allowed from now on: “Builder can email ken@brightpath.test without asking.”"
        self.assertEqual((r["confirmation_title"], r["confirmation"]), ("Added to your Allow rules", line))
        row = store.db().execute("SELECT * FROM feed WHERE title='Added to your Allow rules'").fetchone()
        self.assertEqual((row["bot_id"], row["body"]), ("builder", line))
        req = plugin.action_request("builder", "send", "", "Email riley@gmail.com", "riley@gmail.com", "Hi", "fp2",
                                    "chat-1", None, why="asked", scope="riley@gmail.com")
        r = self.client.post(f"/api/signin-requests/{req}", json={"decision": "once"}).json()
        self.assertIsNone(r["confirmation"])

    def test_always_allow_on_a_sign_in_card_confirms_too(self):
        req = plugin.request("school", "elearning.lakeview.test", "what is due?", "chat-1", None, why="ask")
        r = self.client.post(f"/api/signin-requests/{req}", json={"decision": "always"}).json()
        self.assertEqual(r["confirmation"],
                         "Allowed from now on: “School can sign in to elearning.lakeview.test without asking.”")


class NoAnswerInTime(AppCase):
    def expire(self, req):
        with plugin.db() as c:
            c.execute("UPDATE requests SET created=created-? WHERE id=?", (plugin.REQUEST_TTL_LONG_SEC + 60, req))
        plugin.expire_old()

    def scan(self):
        from taffy_hub import watcher
        for name in ("consent_request", "consent_signin", "consent_action"):
            store.set_cursor(name, 10**6)
        asyncio.run(watcher.scan_consent())

    def test_the_card_and_the_chat_say_so_and_the_zip_hears_it_next_turn(self):
        store.set_cursor("consent_expired_ms", int((time.time() - 5) * 1000))
        req = plugin.action_request("money", "send", "", "Email andrea@x.com about lunch", "andrea@x.com", "Hi",
                                    "fp1", "chat-1", None, why="asked", scope="andrea@x.com")
        self.expire(req)
        r = self.client.post(f"/api/signin-requests/{req}", json={"decision": "once"})
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "No answer in time, so Money didn't do this."))
        self.scan()
        row = store.db().execute("SELECT * FROM feed WHERE kind='signin_decision'").fetchone()
        self.assertEqual((row["bot_id"], row["title"], row["body"]),
                         ("money", "Email andrea@x.com about lunch", "No answer in time, so Money didn't do this."))
        self.scan()
        self.assertEqual(store.db().execute("SELECT COUNT(*) FROM feed WHERE kind='signin_decision'").fetchone()[0], 1)
        ctx = team.context_for("money", "chat-2", first=False)
        self.assertIn("did not answer your card in time", ctx)
        self.assertIn("Email andrea@x.com about lunch", ctx)
        self.assertNotIn("did not answer your card", team.context_for("money", "chat-2", first=False))

    def test_a_board_task_carries_on_and_scheduled_work_is_left_alone(self):
        store.set_cursor("consent_expired_ms", int((time.time() - 5) * 1000))
        task = plugin.request("school", "elearning.lakeview.test", "", None, "t_abc123", why="background")
        sched = plugin.action_request("money", "write", "", "Add a card to Next", "", "", "fp9", "cron-1", None,
                                      why="scheduled", routine={"id": "brief", "name": "Morning brief"})
        self.expire(task)
        self.expire(sched)
        self.scan()
        self.assertEqual(len(self.answered), 1)
        self.assertEqual(self.answered[0][0], "t_abc123")
        self.assertIn("this was not done: Sign in to elearning.lakeview.test", self.answered[0][1])
        self.assertEqual(store.get_setting("card_notes", {}), {})
        bodies = [r["body"] for r in store.db().execute("SELECT body FROM feed WHERE kind='signin_decision'")]
        self.assertEqual(sorted(bodies), ["No answer in time, so Money didn't do this.",
                                          "No answer in time, so School didn't do this."])

    def test_old_expiries_are_not_replayed_on_first_run(self):
        req = plugin.action_request("money", "send", "", "Email andrea@x.com", "andrea@x.com", "Hi", "fp1", "chat-1",
                                    None, why="asked")
        self.expire(req)
        time.sleep(0.01)
        self.scan()
        self.assertEqual(store.db().execute("SELECT COUNT(*) FROM feed WHERE kind='signin_decision'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
