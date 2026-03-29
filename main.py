from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage,
 TextSendMessage
import google.generativeai as genai
import os

app = Flask(__name__)

line_bot_api =
LineBotApi(os.environ['LINE_CHANNEL_ACCESS_TOKEN'])
 handler = WebhookHandler(os.environ['LINE_CHANNEL_SECRET'])
 genai.configure(api_key=os.environ['GEMINI_API_KEY'])

 model = genai.GenerativeModel('gemini-1.5-flash')

ELIZABETH_PROMPT = """あなたはエリザベスです。株式会社L&Bの
 代表・七種珠水さん（ナナさん）の優秀な秘書AIです。丁寧で知的
 、テキパキとした対応が得意です。建築・設計・施工・デザイン事
 業のサポートをします。常に「エリザベスでございます」という口
 調で、プロフェッショナルに対応してください。"""

 @app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
     body = request.get_data(as_text=True)
     try:
        handler.handle(body, signature)
     except InvalidSignatureError:
         abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
 def handle_message(event):
     user_message = event.message.text
     response = model.generate_content(ELIZABETH_PROMPT +
"\n\nナナさん: " + user_message)
     line_bot_api.reply_message(
         event.reply_token,
         TextSendMessage(text=response.text)
     )

if __name__ == "__main__":
     port = int(os.environ.get('PORT', 5000))
     app.run(host='0.0.0.0', port=port)
