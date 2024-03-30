import sublime
import json

from urllib.parse import urlencode
from urllib.request import urlopen

def getResponse(server, text, language, disabledRules, username, apikey):
	payload = {
		'language': language,
		'text': text.encode('utf8'),
		'User-Agent': 'sublime',
		'disabledRules' : ','.join(disabledRules)
	}
	if len(username) > 0 and len(apikey) > 0:
		payload['username'] = username
		payload['apiKey'] = apikey
	content = _post(server, payload)
	if content:
		j = json.loads(content.decode('utf-8'))
		return j['matches']
	else:
		return None

# internal functions:

def _post(server, payload):
	data = urlencode(payload).encode('utf8')
	try:
		content = urlopen(server, data).read()
		return content
	except IOError as e:
		print(e.read())
		return None
