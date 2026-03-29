from flask import Flask, request, abort
from linebot import LineBotApi,
WebhookHandler
from linebot.exceptions import
InvalidSignatureError
from linebot.models import MessageEvent,
TextMessage, TextSendMessage
from google import genai
import os

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ['LINE_
CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['LINE_C
HANNEL_SECRET'])
client = genai.Client(api_key=os.environ['G
EMINI_API_KEY'])

ELIZABETH_PROMPT =
"あなたはエリザベスです。株式会社L&Bの秘書A
Iです。丁寧にお答えします。"

@app.route("/", methods=['GET'])
def health_check():
    return 'OK'

@app.route("/callback", methods=['POST'])
def callback():
    signature =
request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent,
message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    response =
client.models.generate_content(
        model='gemini-2.0-flash-lite',
        contents=ELIZABETH_PROMPT +
"\n\nナナさん: " + user_message
    )
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response.text)
    )

if __name__ == "__main__":
    port = int(os.environ.get('PORT',
5000))
    app.run(host='0.0.0.0', port=port)
