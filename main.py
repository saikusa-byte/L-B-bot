from flask import Flask, request,
abort
import requests
import hmac
import hashlib
import base64
import json
import os

app = Flask(__name__)

LINE_CHANNEL_SECRET =
os.environ['LINE_CHANNEL_SECRET']
LINE_CHANNEL_ACCESS_TOKEN = os.envir
on['LINE_CHANNEL_ACCESS_TOKEN']
GEMINI_API_KEY =
os.environ['GEMINI_API_KEY']

ELIZABETH_PROMPT = 'You are
Elizabeth, AI secretary of L&B
company. Always respond in polite
Japanese.'

@app.route("/", methods=['GET'])
def health_check():
    return 'OK'

@app.route("/callback",
methods=['POST'])
def callback():
    signature = request.headers.get(
'X-Line-Signature', '')
    body =
request.get_data(as_text=True)

    hash = hmac.new(LINE_CHANNEL_SEC
RET.encode('utf-8'),

body.encode('utf-8'),
hashlib.sha256).digest()
    expected = base64.b64encode(hash
).decode('utf-8')
    if signature != expected:
        abort(400)

    data = json.loads(body)
    for event in data.get('events',
[]):
        if event['type'] ==
'message' and
event['message']['type'] == 'text':
            user_message =
event['message']['text']
            reply_token =
event['replyToken']

            gemini_url =
"https://generativelanguage.googleap
is.com/v1beta/models/gemini-1.5-flas
h:generateContent?key=" +
GEMINI_API_KEY
            gemini_data =
{"contents": [{"parts": [{"text":
ELIZABETH_PROMPT + "\n\nナナさん: "
+ user_message}]}]}
            gemini_response =
requests.post(gemini_url,
json=gemini_data)
            reply_text =
gemini_response.json()['candidates']
[0]['content']['parts'][0]['text']

            line_url = "https://api.
line.me/v2/bot/message/reply"
            headers = {
                "Authorization":
"Bearer " +
LINE_CHANNEL_ACCESS_TOKEN,
                "Content-Type":
"application/json"
            }
            line_data =
{"replyToken": reply_token,
"messages": [{"type": "text",
"text": reply_text}]}
            requests.post(line_url,
headers=headers, json=line_data)

    return 'OK'

if __name__ == "__main__":
    port =
int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0',
port=port)
 
