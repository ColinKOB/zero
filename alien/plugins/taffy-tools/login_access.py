""

from . import mac


def allowed(source, handle):
    reply = mac._call("login_access", {"source": str(source), "handle": handle}, 15, area="chat")
    return reply.get("ok") is True and reply.get("allowed") is True


def install():
    try:
        from agent.vault_store import VaultError, VaultStore
    except ImportError:
        return False
    if getattr(VaultStore.resolve_secret, "_zero_access_checked", False):
        return
    original_resolve = VaultStore.resolve_secret
    original_list = VaultStore.list_items
    original_add = VaultStore.add_item

    def resolve(self, handle):
        meta = self.get_meta(handle)
        if meta and meta.kind == "login" and not allowed(self._base, handle):
            raise VaultError("This sign-in is not shared with this Zip, or its access could not be checked.")
        return original_resolve(self, handle)

    def listing(self):
        return [m for m in original_list(self) if m.kind != "login" or allowed(self._base, m.id)]

    def add(self, *args, **kwargs):
        meta = original_add(self, *args, **kwargs)
        if meta.kind == "login":
            reply = mac._call("login_record", {"source": str(self._base), "handle": meta.id}, 15, area="chat")
            if reply.get("ok") is not True:
                raise VaultError("The password was saved, but its sign-in access could not be checked. Try again in a moment.")
        return meta

    resolve._zero_access_checked = True
    VaultStore.resolve_secret = resolve
    VaultStore.list_items = listing
    VaultStore.add_item = add
