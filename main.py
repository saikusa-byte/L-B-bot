from flask import Flask, request,
abort
from linebot import LineBotApi,
WebhookHandler
from linebot.exceptions import
InvalidSignatureError
from linebot.models import
MessageEvent, TextMessage,
TextSendMessage
import requests
import os

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ
['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ[
'LINE_CHANNEL_SECRET'])

ELIZABETH_PROMPT =
"あなたはエリザベスです。株式会社L&B
の秘書AIです。丁寧にお答えします。"

@app.route("/", methods=['GET'])
def health_check():
    return 'OK'

@app.route("/callback",
methods=['POST'])
def callback():
    signature =
request.headers['X-Line-Signature']
    body =
request.get_data(as_text=True)
    try:
        handler.handle(body,
signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent,
message=TextMessage)
def handle_message(event):
    user_message =
event.message.text
    url = f"https://generativelangua
ge.googleapis.com/v1/models/gemini-1
.5-flash:generateContent?key={os.env
iron['GEMINI_API_KEY']}"
    data = {"contents": [{"parts":
[{"text": ELIZABETH_PROMPT +
"\n\nナナさん: " + user_message}]}]}
    response = requests.post(url,
json=data)
    reply_text =
response.json()['candidates'][0]['co
ntent']['parts'][0]['text']
    line_bot_api.reply_message(event
.reply_token,
TextSendMessage(text=reply_text))

if __name__ == "__main__":
    port =
int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0',
port=port)
