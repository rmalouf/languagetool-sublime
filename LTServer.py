import sublime
import json
import socket

from urllib.parse import urlencode, urljoin
from urllib.request import urlopen
from urllib.error import HTTPError, URLError


def getLanguages(server):
    server = urljoin(server, "languages")
    return _connect(server, None)


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
    response = _connect(server, payload)
    if response:
        return response["matches"]
    else:
        return None


def _connect(server, payload):
    try:
        if payload:
            payload = urlencode(payload).encode("utf8")
        content = urlopen(server, payload, timeout=60).read()
        if content:
            response = json.loads(content.decode("utf-8"))
            return response
    except HTTPError as e:
        msg = str(e.code) + " " + e.reason + "\n\n" + e.read().decode("utf-8")
    except URLError:
        msg = "Invalid URL"
    except socket.timeout:
        msg = "Connection timeout"
    except OSError:
        msg = "Unknown error"
    sublime.set_timeout(lambda: _error(msg))


def _error(msg):
    sublime.error_message("LanguageTool Server Error:\n" + msg)
