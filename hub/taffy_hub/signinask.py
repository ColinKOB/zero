""









import logging

from . import shortreply

log = logging.getLogger("taffy.signinask")

TIMEOUT_SEC = 12
PROMPT = ("A helper is pausing a task and wrote this reason for the person it works for, who is called {first}.\n\n"
          "Task: {title}\nReason: {reason}\n\n"
          "Is the reason asking {first} personally to sign in or log in to a website or app so the task can go on? "
          "Someone else signing in, a sign-in page that is broken, or a question about which account to use is not "
          "that.\n\nAnswer with one word: YES, NO or UNSURE.")




CHECK_PROMPT = ("A helper is pausing a task and wrote this reason for the person it works for.\n\n"
                "Task: {title}\nReason: {reason}\n\n"
                "Does the reason say a website's human check is stopping the work right now, even if it also asks about "
                "something else? A human check is a CAPTCHA, a \"verify you are human\" page, a press and hold, or a "
                "similar test that the visitor is a person. A wrong password, a code sent by text, or a page that is "
                "down is not one.\n\nAnswer with one word: YES, NO or UNSURE.")


async def names_check(reason: str, title: str = "", *, zips) -> bool:
    ""
    prompt = CHECK_PROMPT.format(title=" ".join(str(title or "").split())[:200] or "(none)",
                                 reason=" ".join(str(reason or "").split())[:1000])
    try:
        with shortreply.reading_text(prompt, zips):
            answer = await shortreply.ask_helper(prompt, timeout=TIMEOUT_SEC, max_tokens=5, effort="low")
    except Exception as e:
        log.info("could not judge a block for a human check, so it goes through: %s", str(e)[:200])
        return False
    return answer.strip().strip(".").upper() == "YES"


async def asks_him(reason: str, title: str = "", *, zips) -> bool:
    ""
    from . import ownerrules, who
    prompt = PROMPT.format(first=who.first(), title=" ".join(str(title or "").split())[:200] or "(none)",
                           reason=" ".join(str(reason or "").split())[:1000]) \
        + ownerrules.block(ownerrules.SIGNIN, f"{title} {reason}")
    try:
        with shortreply.reading_text(prompt, zips):
            answer = await shortreply.ask_helper(prompt, timeout=TIMEOUT_SEC, max_tokens=5, effort="low")
    except Exception as e:
        log.info("could not judge a sign-in block, so it goes through: %s", str(e)[:200])
        return False
    return answer.strip().strip(".").upper() == "YES"
