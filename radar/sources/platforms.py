"""Recognise the external platforms that hide inside Canvas courses."""
from __future__ import annotations

import re
from urllib.parse import urlparse

# (substring to look for in URL or tool name, display name, category)
PLATFORMS = [
    ("labflow", "LabFlow", "tool"),
    ("catalystedu", "LabFlow", "tool"),
    ("gradescope", "Gradescope", "tool"),
    ("mylab", "Pearson MyLab", "tool"),
    ("mastering", "Pearson Mastering", "tool"),
    ("pearson", "Pearson", "tool"),
    ("revel", "Pearson Revel", "tool"),
    ("mheducation", "McGraw Hill Connect", "tool"),
    ("connect.mheducation", "McGraw Hill Connect", "tool"),
    ("aleks", "ALEKS", "tool"),
    ("webassign", "WebAssign", "tool"),
    ("cengage", "Cengage", "tool"),
    ("mindtap", "Cengage MindTap", "tool"),
    ("zybooks", "zyBooks", "tool"),
    ("tophat", "Top Hat", "tool"),
    ("piazza", "Piazza", "tool"),
    ("edstem", "Ed Discussion", "tool"),
    ("edstem.org", "Ed Discussion", "tool"),
    ("packback", "Packback", "tool"),
    ("perusall", "Perusall", "tool"),
    ("macmillan", "Macmillan Achieve", "tool"),
    ("achieve", "Macmillan Achieve", "tool"),
    ("wiley", "WileyPLUS", "tool"),
    ("expertta", "Expert TA", "tool"),
    ("sapling", "Sapling", "tool"),
    ("knewton", "Knewton Alta", "tool"),
    ("launchpad", "LaunchPad", "tool"),
    ("codio", "Codio", "tool"),
    ("replit", "Replit", "tool"),
    ("classroom.github", "GitHub Classroom", "tool"),
    ("github.com", "GitHub", "tool"),
    ("codepost", "codePost", "tool"),
    ("codegrade", "CodeGrade", "tool"),
    ("autolab", "Autolab", "tool"),
    ("prairielearn", "PrairieLearn", "tool"),
    ("webwork", "WeBWorK", "tool"),
    ("mymathlab", "MyMathLab", "tool"),
    ("hawkeslearning", "Hawkes Learning", "tool"),
    ("lon-capa", "LON-CAPA", "tool"),
    ("smartwork", "Smartwork", "tool"),
    ("inquizitive", "InQuizitive", "tool"),
    ("wwnorton", "Norton", "tool"),
    ("turnitin", "Turnitin", "tool"),
    ("respondus", "Respondus", "tool"),
    ("honorlock", "Honorlock", "tool"),
    ("proctorio", "Proctorio", "tool"),
    ("polleverywhere", "Poll Everywhere", "tool"),
    ("iclicker", "iClicker", "tool"),
    ("kahoot", "Kahoot", "tool"),
    ("gradebook", "Gradebook", "tool"),
    ("vocareum", "Vocareum", "tool"),
    ("jupyterhub", "JupyterHub", "tool"),
    ("colab.research", "Google Colab", "tool"),
    ("overleaf", "Overleaf", "tool"),
    ("slack.com", "Slack", "tool"),
    ("discord", "Discord", "tool"),
    ("teams.microsoft", "Microsoft Teams", "tool"),
    ("docs.google", "Google Docs", "link"),
    ("drive.google", "Google Drive", "link"),
    ("forms.gle", "Google Form", "link"),
    ("forms.office", "Microsoft Form", "link"),
    ("qualtrics", "Qualtrics", "link"),
    ("calendly", "Calendly", "link"),
    ("zoom.us", "Zoom", "media"),
    ("youtube", "YouTube", "media"),
    ("youtu.be", "YouTube", "media"),
    ("vimeo", "Vimeo", "media"),
    ("kaltura", "Kaltura", "media"),
    ("panopto", "Panopto", "media"),
    ("echo360", "Echo360", "media"),
    ("mediaspace", "Kaltura", "media"),
]

IGNORE_SCHEMES = ("mailto:", "javascript:", "tel:", "data:", "#")
IGNORE_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|woff2?)(\?.*)?$", re.I)


def detect_platform(url: str | None, name: str | None = None) -> tuple[str | None, str]:
    """Return (platform display name or None, category)."""
    hay = f"{url or ''} {name or ''}".lower()
    for needle, label, cat in PLATFORMS:
        if needle in hay:
            return label, cat
    return None, "link"


def normalize_url(url: str) -> str:
    url = url.strip()
    url = re.sub(r"[?&](utm_[a-z]+|fbclid|gclid)=[^&#]*", "", url)
    url = url.rstrip("?&")
    return url


def is_interesting_link(url: str, canvas_host: str | None) -> bool:
    if not url or url.lower().startswith(IGNORE_SCHEMES):
        return False
    if not re.match(r"^https?://", url, re.I):
        return False
    if IGNORE_EXT.search(url):
        return False
    host = (urlparse(url).hostname or "").lower()
    if canvas_host and (host == canvas_host.lower() or host.endswith("." + canvas_host.lower())):
        # Links back into Canvas are navigation, not external sites - except LTI launches.
        return "/external_tools/" in url
    if host.endswith("instructure.com") or host.endswith("canvaslms.com"):
        return "/external_tools/" in url
    return True
