from flask import Flask, request, abort
import requests
import hmac
import hashlib
import base64
import json
import os

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
GEMINI_API_KEY = os.environ['GEMINI_API_KEY']

ELIZABETH_PROMPT = "You are Elizabeth, AI secretary of L&B company. Always respond in polite Japanese."

@app.route("/", methods=['GET'])
def health_check():
    return 'OK'

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    h = hmac.new(
        LINE_CHANNEL_SECRET.encode('utf-8'),
        body.encode('utf-8'),
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(h).decode('utf-8')
    if signature != expected:
        abort(400)

    data = json.loads(body)
    for event in data.get('events', []):
        if event['type'] == 'message' and event['message']['type'] == 'text':
            user_message = event['message']['text']
            reply_token = event['replyToken']

            base_url = "https://generativelanguage.googleapis.com"
            model_path = "/v1/models/gemini-1.5-flash:generateContent"
            gemini_url = base_url + model_path + "?key=" + GEMINI_API_KEY

            prompt = ELIZABETH_PROMPT + "\n\nナナさん: " + user_message
            gemini_data = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            gemini_response = requests.post(gemini_url, json=gemini_data)
            gemini_json = gemini_response.json()
            print("Gemini response:", gemini_json)
            if 'candidates' not in gemini_json:
                reply_text = "エラーが発生しました: " + str(gemini_json.get('error', {}).get('message', '不明なエラー'))
            else:
                reply_text = gemini_json['candidates'][0]['content']['parts'][0]['text']

            line_url = "https://api.line.me/v2/bot/message/reply"
            headers = {
                "Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN,
                "Content-Type": "application/json"
            }
            line_data = {
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": reply_text}]
            }
            requests.post(line_url, headers=headers, json=line_data)

    return 'OK'

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
