# dmgbuild settings for Zero.dmg. release.sh passes -D app=<path to Zero.app> -D background=<this folder>/background.tiff.
# The window shows Zero on the left, the Applications folder on the right, and background.tiff, which says
# "Drag Zero to Applications" with an arrow between them. dmgbuild writes Finder's layout file itself, so no
# Finder window or Automation permission is needed. https://dmgbuild.readthedocs.io
app = defines["app"]  # noqa: F821  (dmgbuild provides `defines`)

format = "UDZO"
filesystem = "HFS+"
files = [(app, "Zero.app")]
symlinks = {"Applications": "/Applications"}
background = defines["background"]  # noqa: F821
window_rect = ((200, 160), (640, 400))
icon_size = 112
text_size = 13
icon_locations = {"Zero.app": (170, 215), "Applications": (470, 215)}
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
default_view = "icon-view"
show_icon_preview = False
hide_extension = ["Zero.app"]
