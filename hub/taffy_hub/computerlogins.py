""

from . import config, screen

MESSAGES = {
    "control_required": "Take over the computer before using a saved login.",
    "control_changed": "Control changed. Open saved logins again.",
    "choice_required": "Choose a saved login first.",
    "choice_expired": "This choice expired. Open saved logins again.",
    "login_removed": "That saved login is no longer available.",
    "login_unavailable": "That saved login could not be used.",
    "page_changed": "The page changed. Open saved logins again.",
    "fill_uncertain": "The fill could not be confirmed. Check the page before trying again.",
    "open_page": "Open the sign-in page on the computer first.",
    "browser_unavailable": "The browser did not answer. Try again.",
    "form_changed": "The form changed. Check the page before trying again.",
    "focus_form": "Click the sign-in form on the computer, then open saved logins.",
    "no_fields": "I could not find a sign-in field on this page.",
    "unavailable": "Saved logins are not ready on this computer yet.",
}


class NotReady(Exception):
    def __init__(self, code):
        self.status = 403 if code in ("control_required", "control_changed") else 409
        super().__init__(MESSAGES.get(code, MESSAGES["unavailable"]))


def request(bot_id: str, action: str, choice: str = "") -> dict:
    try:
        from gateway.control_socket import query_gateway_control
        result = query_gateway_control(config.HERMES_HOME, "owner-vault-autofill", params={
            "home": str(screen.profile_home(bot_id)), "action": action, "choice": choice}, timeout=15)
    except Exception:
        result = None
    if not isinstance(result, dict) or result.get("ok") is not True:
        code = "fill_uncertain" if isinstance(result, dict) and result.get("may_have_filled") is True else (result.get("code") if isinstance(result, dict) else "unavailable")
        raise NotReady(code)
    if action == "fill":
        return {"fields": [field for field in result.get("fields", []) if field in ("username", "password")]}
    fields = ("id", "label", "username", "profile")
    items = [{**{field: row.get(field) if isinstance(row.get(field), str) else "" for field in fields},
              "own": row.get("own") is True} for row in result.get("items", []) if isinstance(row, dict)]
    return {"origin": result.get("origin", ""), "items": items}
