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

ELIZABETH_PROMPT = """あなたはエリザベスです。株式会社L&Bの専属AIアシスタント秘書です。
常に丁寧な日本語で、簡潔かつ的確に応答してください。

以下はナナ（七種珠水）についての引き継ぎ情報です。これを深く理解した上で対応してください。

【ナナについて】
L&Bの代表。空間ブランディングから設計・施工までを一貫して行う会社のコンセプト責任者。
主な役割：コンセプト設計、クライアントの本質的課題の抽出、ブランドと空間の接続設計、プロジェクト全体の最終判断。

【強み】
抽象的な概念を空間として具体化できる。コンセプトを軸に全体を統合できる。色・光・素材による感情設計が得意。

【思考・価値観】
デザインは「本質の追求」。美しさだけでなく成果（売上・ブランド価値）に責任を持つ。表層ではなく構造や関係性から物事を見る。中途半端なものやコントロールできない仕事は避ける。

【現在の課題】
デザイン品質を維持できる人材不足、長時間労働の改善、組織体制の未整備（PM・広報・CFOなど）、プロジェクト管理・トラブル防止体制の弱さ、ブランドの言語化・発信力の不足。

【目標】
L&Bを世界トップレベルのデザイン会社にする。売上100億円規模まで成長。日本に「空間に投資する文化」をつくる。

【性格・傾向】
意思決定が速く直感的。納得できないことには強いストレスを感じる。責任感が強く問題を自分で抱え込みやすい。他責的な言動や不誠実さに強く反応する。

【エリザベスへの期待】
思考整理・言語化の補助。意思決定の壁打ち（異なる視点の提示を含む）。プロジェクトの構造整理とリスク指摘。感情ではなく事実ベースでの判断支援。

ナナの思考を再現・補助し、判断を加速することがエリザベスの最重要役割です。"""

# ユーザーごとの会話履歴（最大20件）
conversation_history = {}

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
            user_id = event['source']['userId']

            if user_id not in conversation_history:
                conversation_history[user_id] = []

            conversation_history[user_id].append({
                "role": "user",
                "parts": [{"text": user_message}]
            })

            if len(conversation_history[user_id]) > 20:
                conversation_history[user_id] = conversation_history[user_id][-20:]

            base_url = "https://generativelanguage.googleapis.com"
            model_path = "/v1beta/models/gemini-2.5-flash:generateContent"
            gemini_url = base_url + model_path + "?key=" + GEMINI_API_KEY

            gemini_data = {
                "system_instruction": {
                    "parts": [{"text": ELIZABETH_PROMPT}]
                },
                "contents": conversation_history[user_id]
            }

            try:
                gemini_response = requests.post(gemini_url, json=gemini_data)
                gemini_json = gemini_response.json()
                if 'candidates' not in gemini_json:
                    reply_text = "エラー: " + str(gemini_json.get('error', {}).get('message', str(gemini_json)))
                else:
                    reply_text = gemini_json['candidates'][0]['content']['parts'][0]['text']
                    conversation_history[user_id].append({
                        "role": "model",
                        "parts": [{"text": reply_text}]
                    })
            except Exception as e:
                print("Exception:", str(e))
                reply_text = "例外エラー: " + str(e)

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
