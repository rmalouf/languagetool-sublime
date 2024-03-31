import sublime
import json
import socket

from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError, URLError


def getResponse(server, data, language, disabledRules, username, apikey):
    payload = {
        "language": language,
        "data": data.encode("utf8"),
        "User-Agent": "sublime",
        "disabledRules": ",".join(disabledRules),
    }
    if len(username) > 0 and len(apikey) > 0:
        payload["username"] = username
        payload["apiKey"] = apikey
    content = _post(server, payload)
    if content:
        j = json.loads(content.decode("utf-8"))
        return j["matches"]
    else:
        return None


def _post(server, payload):
    try:
        data = urlencode(payload).encode("utf8")
        content = urlopen(server, data, timeout=60).read()
        return content
    except HTTPError as e:
        msg = str(e.code) + " " + e.reason + "\n\n" + e.read().decode("utf-8")
    except URLError:
        msg = "Invalid URL"
    except socket.timeout:
        msg = "Connection timeout"
    except OSError:
        msg = "Unknown error"
    sublime.set_timeout(lambda: _error(msg), 10)
    return None


def _error(msg):
    sublime.error_message("LanguageTool Server Error:\n" + msg)
