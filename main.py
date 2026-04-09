from flask import Flask, request, abort
import requests
import hmac
import hashlib
import base64
import json
import os
import re
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
GEMINI_API_KEY = os.environ['GEMINI_API_KEY']
UPSTASH_REDIS_REST_URL = os.environ['UPSTASH_REDIS_REST_URL']
UPSTASH_REDIS_REST_TOKEN = os.environ['UPSTASH_REDIS_REST_TOKEN']
NANA_LINE_USER_ID = os.environ.get('NANA_LINE_USER_ID', '')

JST = timezone(timedelta(hours=9))
SPREADSHEET_ID = os.environ.get('GOOGLE_SPREADSHEET_ID', '')
P1_SPREADSHEET_ID = os.environ.get('P1_SPREADSHEET_ID', '')

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

ナナの思考を再現・補助し、判断を加速することがエリザベスの最重要役割です。

【スケジュール管理】
会話の中に日時と内容を含む予定（会議、打合せ、アポイント、締め切り、訪問など）が含まれる場合、通常の返答に加えて必ず最後に以下の形式で追記してください：
[[SCHEDULE:{"date":"YYYY-MM-DD","time":"HH:MM","title":"予定名"}]]
時刻が不明な場合はtimeを""にしてください。
ユーザーが「予定を見せて」「スケジュールは？」「今週の予定は？」「予定一覧」などと聞いた場合は、返答に[[SHOW_SCHEDULE]]を含めてください。
予定の削除を求められた場合は[[DELETE_SCHEDULE:番号]]を含めてください（番号は1始まり）。"""


# ============================================================
# Google Sheets ヘルパー
# ============================================================

# 列: 日付(1),名前(2),出社時刻(3),退社時刻(4),勤務時間(5),体調(朝)(6),本日タスク(7),体調(夜)(8),完了タスク(9),共有事項(10)
SHEET_HEADERS = ['日付', '名前', '出社時刻', '退社時刻', '勤務時間', '体調(朝)', '本日タスク', '体調(夜)', '完了タスク', '共有事項']

def get_sheet():
    creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
    if not creds_json or not SPREADSHEET_ID:
        return None
    try:
        creds_dict = json.loads(creds_json)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.sheet1
        if not sheet.row_values(1):
            sheet.append_row(SHEET_HEADERS)
        return sheet
    except Exception as e:
        print("Sheets error:", e)
        return None


def find_row(sheet, date, name):
    try:
        all_values = sheet.get_all_values()
        for i, row in enumerate(all_values[1:], 2):
            if len(row) >= 2 and row[0] == date and row[1] == name:
                return i
    except Exception:
        pass
    return None


def calc_overtime_minutes(checkout_time):
    """18時以降の残業時間を分単位で計算"""
    try:
        checkout = datetime.strptime(checkout_time, "%H:%M")
        standard_end = datetime.strptime("18:00", "%H:%M")
        if checkout > standard_end:
            diff = checkout - standard_end
            return int(diff.total_seconds() // 60)
    except Exception:
        pass
    return 0


def write_morning_to_sheet(date, name, check_in_time, health_score, tasks):
    try:
        sheet = get_sheet()
        if not sheet:
            return
        row_idx = find_row(sheet, date, name)
        task_str = ' / '.join(tasks) if tasks else ''
        if row_idx:
            sheet.update_cell(row_idx, 3, check_in_time)  # 出社時刻
            sheet.update_cell(row_idx, 6, str(health_score))  # 体調(朝)
            sheet.update_cell(row_idx, 7, task_str)  # 本日タスク
        else:
            sheet.append_row([date, name, check_in_time, '', '', str(health_score), task_str, '', '', ''])
    except Exception as e:
        print("Sheets morning write error:", e)


def write_evening_to_sheet(date, name, checkout_time, work_hours, health_score, completed_tasks, shared):
    try:
        sheet = get_sheet()
        if not sheet:
            return
        row_idx = find_row(sheet, date, name)
        tasks_str = ' / '.join(completed_tasks) if completed_tasks else ''
        if row_idx:
            sheet.update_cell(row_idx, 4, checkout_time)   # 退社時刻
            sheet.update_cell(row_idx, 5, work_hours)       # 勤務時間
            sheet.update_cell(row_idx, 8, str(health_score))  # 体調(夜)
            sheet.update_cell(row_idx, 9, tasks_str)           # 完了タスク
            sheet.update_cell(row_idx, 10, shared)             # 共有事項
        else:
            sheet.append_row([date, name, '', checkout_time, work_hours, '', '', str(health_score), tasks_str, shared])
    except Exception as e:
        print("Sheets evening write error:", e)


def update_monthly_summary(year_month=None):
    """月次集計シートを更新する（例: year_month='2026-04'）"""
    try:
        creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
        if not creds_json or not SPREADSHEET_ID:
            return
        creds_dict = json.loads(creds_json)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)

        # 勤怠記録シートからデータ取得
        raw_sheet = spreadsheet.sheet1
        all_rows = raw_sheet.get_all_values()
        if len(all_rows) <= 1:
            return

        if not year_month:
            year_month = datetime.now(JST).strftime("%Y-%m")

        # 対象月のデータを集計
        # 列(0始まり): 日付[0],名前[1],出社[2],退社[3],勤務時間[4],体調朝[5],本日タスク[6],体調夜[7],完了タスク[8],共有[9]
        summary = {}
        for row in all_rows[1:]:
            if len(row) < 3:
                continue
            if not row[0].startswith(year_month):
                continue
            name = row[1]
            if not name:
                continue
            if name not in summary:
                summary[name] = {'days': 0, 'total_minutes': 0, 'am_scores': [], 'pm_scores': []}

            # 出社日数
            if row[2]:
                summary[name]['days'] += 1

            # 勤務時間
            if len(row) > 4 and row[4]:
                m = re.search(r'(\d+)時間(\d+)分', row[4])
                if m:
                    summary[name]['total_minutes'] += int(m.group(1)) * 60 + int(m.group(2))

            # 体調点数(朝) → index 5
            try:
                if len(row) > 5 and row[5]:
                    summary[name]['am_scores'].append(float(row[5]))
            except Exception:
                pass
            # 体調点数(夜) → index 7
            try:
                if len(row) > 7 and row[7]:
                    summary[name]['pm_scores'].append(float(row[7]))
            except Exception:
                pass

        if not summary:
            return

        # 月次集計シートを取得または作成
        try:
            monthly_sheet = spreadsheet.worksheet("月次集計")
        except Exception:
            monthly_sheet = spreadsheet.add_worksheet(title="月次集計", rows=100, cols=10)

        # ヘッダー設定
        headers = ['年月', '名前', '出社日数', '合計勤務時間', '平均体調(朝)', '平均体調(夜)']
        existing = monthly_sheet.get_all_values()
        if not existing or not existing[0]:
            monthly_sheet.append_row(headers)
            existing = [headers]

        # 既存データから対象月の行番号を探して更新 or 追加
        for name, data in summary.items():
            total_h = data['total_minutes'] // 60
            total_m = data['total_minutes'] % 60
            total_str = f"{total_h}時間{total_m}分"
            am_avg = round(sum(data['am_scores']) / len(data['am_scores']), 1) if data['am_scores'] else ''
            pm_avg = round(sum(data['pm_scores']) / len(data['pm_scores']), 1) if data['pm_scores'] else ''
            new_row = [year_month, name, data['days'], total_str, am_avg, pm_avg]

            # 既存行を探す
            found = False
            for i, row in enumerate(existing[1:], 2):
                if len(row) >= 2 and row[0] == year_month and row[1] == name:
                    monthly_sheet.update(f'A{i}:F{i}', [new_row])
                    found = True
                    break
            if not found:
                monthly_sheet.append_row(new_row)

    except Exception as e:
        print("Monthly summary error:", e)


# ============================================================
# Redis ヘルパー
# ============================================================

def redis_cmd(*args):
    headers = {"Authorization": "Bearer " + UPSTASH_REDIS_REST_TOKEN}
    try:
        resp = requests.post(UPSTASH_REDIS_REST_URL, headers=headers, json=list(args))
        return resp.json().get("result")
    except Exception:
        return None


def redis_get(key):
    result = redis_cmd("GET", key)
    if result:
        try:
            return json.loads(result)
        except Exception:
            return result
    return None


def redis_set(key, value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    redis_cmd("SET", key, str(value))


def redis_get_conv(user_id):
    result = redis_get("conv:" + user_id)
    return result if isinstance(result, list) else []


def redis_set_conv(user_id, history):
    redis_cmd("SET", "conv:" + user_id, json.dumps(history, ensure_ascii=False))


def redis_get_schedules(user_id):
    result = redis_get("schedule:" + user_id)
    return result if isinstance(result, list) else []


def redis_set_schedules(user_id, schedules):
    redis_cmd("SET", "schedule:" + user_id, json.dumps(schedules, ensure_ascii=False))


# ============================================================
# LINE API ヘルパー
# ============================================================

def get_line_profile_name(user_id, group_id=None):
    if group_id:
        url = f"https://api.line.me/v2/bot/group/{group_id}/member/{user_id}"
    else:
        url = f"https://api.line.me/v2/bot/profile/{user_id}"
    headers = {"Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN}
    try:
        resp = requests.get(url, headers=headers)
        return resp.json().get("displayName", "スタッフ")
    except Exception:
        return "スタッフ"


def reply_message(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    data = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    requests.post(url, headers=headers, json=data)


def push_message(to_id, text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    data = {
        "to": to_id,
        "messages": [{"type": "text", "text": text}]
    }
    requests.post(url, headers=headers, json=data)


# ============================================================
# 勤怠ヘルパー
# ============================================================

def today_jst():
    return datetime.now(JST).strftime("%Y-%m-%d")


def register_staff(user_id, name):
    redis_set(f"staff:{user_id}:name", name)
    staff_ids = redis_get("staff_ids") or []
    if not isinstance(staff_ids, list):
        staff_ids = []
    if user_id not in staff_ids:
        staff_ids.append(user_id)
        redis_set("staff_ids", staff_ids)


def parse_morning_report(text):
    result = {"raw": text}
    m = re.search(r'体調[管理]*\n(\d+)点', text)
    if m:
        result["health_score"] = m.group(1)
    m2 = re.search(r'タスク[（(][^)）]*[)）]\n(.*?)(?:\n[①-⑩]|\Z)', text, re.DOTALL)
    if m2:
        tasks = [t.strip() for t in m2.group(1).strip().split('\n') if t.strip()]
        result["tasks"] = tasks
    m3 = re.search(r'③共有事項\n?(.*?)$', text, re.DOTALL)
    if m3:
        result["shared"] = m3.group(1).strip()
    return result


def parse_evening_report(text):
    result = {"raw": text}
    m = re.search(r'体調[パフォーマンス]*点\n(\d+)点', text)
    if m:
        result["health_score"] = m.group(1)
    m2 = re.search(r'退出時間[：:]\s*(\d+)[：:](\d+)', text)
    if m2:
        result["checkout_time"] = f"{m2.group(1)}:{m2.group(2)}"
    m3 = re.search(r'完了タスク[^)\n]*\n(.*?)(?:\n[④-⑩]|\Z)', text, re.DOTALL)
    if m3:
        tasks = [t.strip().lstrip('・') for t in m3.group(1).strip().split('\n') if t.strip()]
        result["completed_tasks"] = tasks
    return result


def save_morning_report(user_id, name, report_data, timestamp_ms):
    date = today_jst()
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=JST)
    data = {
        "user_id": user_id,
        "name": name,
        "date": date,
        "check_in_time": dt.strftime("%H:%M"),
        "health_score": report_data.get("health_score", "?"),
        "tasks": report_data.get("tasks", []),
        "shared": report_data.get("shared", ""),
    }
    redis_set(f"att:{date}:{user_id}:am", data)
    register_staff(user_id, name)
    write_morning_to_sheet(date, name, data["check_in_time"], data["health_score"], data["tasks"])
    check_health_streak(user_id, name, data["health_score"])


def save_evening_report(user_id, name, report_data, timestamp_ms):
    date = today_jst()
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=JST)
    checkout_time = report_data.get("checkout_time") or dt.strftime("%H:%M")

    work_hours = None
    am_data = redis_get(f"att:{date}:{user_id}:am")
    if am_data and am_data.get("check_in_time"):
        try:
            checkin_dt = datetime.strptime(f"{date} {am_data['check_in_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            checkout_dt = datetime.strptime(f"{date} {checkout_time}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            diff = checkout_dt - checkin_dt
            if diff.total_seconds() > 0:
                h = int(diff.total_seconds() // 3600)
                m = int((diff.total_seconds() % 3600) // 60)
                work_hours = f"{h}時間{m}分"
        except Exception:
            pass

    data = {
        "user_id": user_id,
        "name": name,
        "date": date,
        "report_time": dt.strftime("%H:%M"),
        "checkout_time": checkout_time,
        "health_score": report_data.get("health_score", "?"),
        "completed_tasks": report_data.get("completed_tasks", []),
        "shared": report_data.get("shared", ""),
        "work_hours": work_hours or "計算不可",
    }
    redis_set(f"att:{date}:{user_id}:pm", data)
    register_staff(user_id, name)
    write_evening_to_sheet(date, name, checkout_time, data["work_hours"], data["health_score"], data["completed_tasks"], data["shared"])
    update_monthly_summary(date[:7])


# ============================================================
# Gemini ヘルパー
# ============================================================

def gemini_generate(prompt_text):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + GEMINI_API_KEY
    data = {"contents": [{"parts": [{"text": prompt_text}]}]}
    try:
        resp = requests.post(url, json=data)
        return resp.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception:
        return None


def gemini_chat(history):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + GEMINI_API_KEY
    data = {
        "system_instruction": {"parts": [{"text": ELIZABETH_PROMPT}]},
        "contents": history
    }
    try:
        resp = requests.post(url, json=data)
        return resp.json()
    except Exception:
        return {}


def format_schedules(schedules):
    if not schedules:
        return "現在、登録されている予定はありません。"
    lines = ["📅 登録中の予定一覧\n"]
    for i, s in enumerate(schedules, 1):
        time_str = " " + s.get("time", "") if s.get("time") else ""
        lines.append(f"{i}. {s.get('date','')}{time_str}　{s.get('title','')}")
    return "\n".join(lines)


# ============================================================
# サマリー・応援メッセージ
# ============================================================

def build_morning_summary():
    date = today_jst()
    staff_ids = redis_get("staff_ids") or []
    if not isinstance(staff_ids, list):
        staff_ids = []

    reported = []
    not_reported = []

    for uid in staff_ids:
        name = redis_get(f"staff:{uid}:name") or "スタッフ"
        am_data = redis_get(f"att:{date}:{uid}:am")
        if am_data:
            health = am_data.get("health_score", "?")
            tasks = am_data.get("tasks", [])
            task_lines = "\n    ".join(tasks[:4]) if tasks else "（記載なし）"
            reported.append(f"✅ {name}（体調{health}点）\n    {task_lines}")
        else:
            not_reported.append(f"⚠️ {name}")

    lines = [f"🌅 おはようございます、ナナさん。\n{date} 朝の報告まとめです。\n"]
    if reported:
        lines.append("【報告済み】")
        lines.extend(reported)
    if not_reported:
        lines.append("\n【未報告】")
        lines.extend(not_reported)
    if not staff_ids:
        lines.append("まだスタッフの報告が届いていません。")

    return "\n".join(lines)


def build_evening_summary():
    date = today_jst()
    staff_ids = redis_get("staff_ids") or []
    if not isinstance(staff_ids, list):
        staff_ids = []

    reported_pm = []
    not_reported = []

    for uid in staff_ids:
        name = redis_get(f"staff:{uid}:name") or "スタッフ"
        am_data = redis_get(f"att:{date}:{uid}:am")
        pm_data = redis_get(f"att:{date}:{uid}:pm")

        if pm_data:
            health = pm_data.get("health_score", "?")
            work_hours = pm_data.get("work_hours", "不明")
            checkout = pm_data.get("checkout_time", "不明")
            tasks = pm_data.get("completed_tasks", [])
            task_lines = "・" + "\n  ・".join(tasks[:4]) if tasks else "（記載なし）"
            reported_pm.append(
                f"✅ {name}\n"
                f"  体調：{health}点 | 勤務：{work_hours} | 退出：{checkout}\n"
                f"  完了タスク：\n  {task_lines}"
            )
        elif am_data:
            not_reported.append(f"⚠️ {name}（朝は報告あり・日報なし）")
        else:
            not_reported.append(f"❌ {name}（終日未報告）")

    lines = [f"🌙 お疲れ様です、ナナさん。\n{date} 夜の報告まとめです。\n"]
    if reported_pm:
        lines.append("【日報済み】")
        lines.extend(reported_pm)
    if not_reported:
        lines.append("\n【未報告・欠勤】")
        lines.extend(not_reported)
    if not staff_ids:
        lines.append("本日のスタッフ報告はありませんでした。")

    return "\n".join(lines)


def send_morning_greeting_to_groups():
    """毎朝グループへエリザベスからの声掛けを送る"""
    group_ids = redis_get("group_ids") or []
    if not isinstance(group_ids, list):
        return
    date = today_jst()
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    weekday = weekdays[datetime.now(JST).weekday()]
    prompt = (
        f"あなたはエリザベスです。株式会社L&Bの専属AIアシスタント秘書です。\n"
        f"今日は{date}（{weekday}曜日）です。\n"
        f"スタッフ全員への朝の声掛けメッセージを作成してください。\n"
        f"毎日違う内容で、元気が出る・仕事への意欲が湧く内容にしてください。\n"
        f"L&Bは空間デザイン・建築・施工の会社です。\n"
        f"1〜2文で短く。絵文字を1つ使って明るく元気よく。"
    )
    msg = gemini_generate(prompt)
    if msg:
        greeting = f"🌅 おはようございます！\n\n{msg}"
        for gid in group_ids:
            push_message(gid, greeting)


def check_health_streak(user_id, name, health_score):
    """体調10点が10日連続かチェックして褒める"""
    if str(health_score) != '10':
        return
    JST_now = datetime.now(JST)
    streak = 0
    for i in range(10):
        d = (JST_now - timedelta(days=i)).strftime("%Y-%m-%d")
        am_data = redis_get(f"att:{d}:{user_id}:am")
        if am_data and str(am_data.get("health_score", "")) == '10':
            streak += 1
        else:
            break
    if streak >= 10:
        prompt = (
            f"あなたはエリザベスです。株式会社L&Bの専属AIアシスタント秘書です。\n"
            f"{name}さんが体調パフォーマンス10点を10日連続達成しました！\n"
            f"プロとしての自己管理を称える、心から感動した特別な褒めメッセージを作成してください。\n"
            f"具体的に「10日連続」という事実を盛り込み、4〜5文で。"
        )
        msg = gemini_generate(prompt)
        if msg:
            push_message(user_id, f"🏆 特別表彰！\n\n{msg}")


def send_encouraging_messages():
    date = today_jst()
    staff_ids = redis_get("staff_ids") or []
    if not isinstance(staff_ids, list):
        staff_ids = []

    for uid in staff_ids:
        name = redis_get(f"staff:{uid}:name") or "スタッフ"
        pm_data = redis_get(f"att:{date}:{uid}:pm")
        am_data = redis_get(f"att:{date}:{uid}:am")

        if not pm_data and not am_data:
            continue

        # 報告内容の詳細を構築
        context = f"スタッフ名：{name}\n日付：{date}\n"
        report_quality_notes = []

        if am_data:
            tasks = am_data.get("tasks", [])
            health_am = am_data.get("health_score", "")
            context += f"【朝の報告】\n体調：{health_am}点\n本日タスク：{', '.join(tasks)}\n"
            if len(tasks) >= 3:
                report_quality_notes.append("タスクが具体的に複数書かれている")
            if len(tasks) < 2:
                report_quality_notes.append("タスクの記載が少なめ")

        if pm_data:
            tasks = pm_data.get("completed_tasks", [])
            work_hours = pm_data.get("work_hours", "")
            health_pm = pm_data.get("health_score", "")
            shared = pm_data.get("shared", "")
            context += f"【日報】\n体調：{health_pm}点\n勤務時間：{work_hours}\n完了タスク：{', '.join(tasks)}\n共有事項：{shared}\n"
            if len(tasks) >= 3:
                report_quality_notes.append("完了タスクが具体的に書かれている")
            if shared and len(shared) > 10:
                report_quality_notes.append("共有事項もしっかり記載されている")
            if not shared:
                report_quality_notes.append("共有事項の記載がない")
        elif am_data:
            context += "※本日は日報の提出がありませんでした。\n"
            report_quality_notes.append("日報の提出がなかった")

        quality_str = "・".join(report_quality_notes) if report_quality_notes else ""

        # 日付から褒めるポイントをローテーション
        praise_angles = [
            "タスクへの取り組み姿勢や仕事の丁寧さ",
            "体調管理・コンディションへの意識の高さ",
            "報告の誠実さや情報共有の姿勢",
            "仕事量や粘り強さ・継続力",
            "チームへの貢献や周囲への気配り",
            "成長の軌跡や日々の積み重ね",
            "プロ意識や仕事への誠実な向き合い方",
        ]
        day_index = datetime.now(JST).day % len(praise_angles)
        praise_focus = praise_angles[day_index]

        prompt = (
            f"あなたはエリザベスです。株式会社L&Bの専属AIアシスタント秘書です。\n"
            f"社長（七種珠水）の気持ちと言葉を代弁して、{name}さんへ個別メッセージを送ります。\n"
            f"社長はデザインと本質を大切にし、スタッフの成長を心から願っています。\n\n"
            f"以下の報告内容を踏まえて：\n{context}\n"
            f"報告の特徴：{quality_str}\n\n"
            f"【今日特に注目して褒めるポイント】\n{praise_focus}\n\n"
            f"【メッセージの構成】\n"
            f"①今日の仕事への労い（具体的な内容に触れる）\n"
            f"②上記の注目ポイントを中心に、{name}さんの良いところを具体的に褒める\n"
            f"③改善点があれば前向きに優しく一言\n"
            f"④社長らしい温かい明日への励まし\n\n"
            f"毎日違う視点から褒めることを意識して、新鮮で心に響くメッセージにしてください。\n"
            f"全体で5〜6文。温かく前向きなトーンで。末尾に「社長より」と添えてください。"
        )

        msg = gemini_generate(prompt)
        if msg:
            push_message(uid, f"💌 エリザベスより\n\n{msg}")


# ============================================================
# P1報告内容
# ============================================================

P1_HEADERS = ['記録日時', '投稿者', '①日時', '②案件名', '③概要', '④内容', '⑤原因', '⑥クライアント対応策', '⑦関係者対応策', '⑧社内改善策']


def parse_p1_report(text):
    """①〜⑧フォーマットのP1事例を行単位で解析する"""
    fields = ['date', 'project', 'summary', 'content', 'cause', 'client', 'partners', 'internal']
    markers = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧']

    result = {}
    current_field = None
    current_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        matched = False
        for marker, field in zip(markers, fields):
            if marker in stripped:
                if current_field is not None:
                    result[current_field] = '\n'.join(current_lines).strip()
                current_field = field
                m = re.search(r'[：:]\s*(.*)', stripped)
                current_lines = [m.group(1).strip()] if m else []
                matched = True
                break
        if not matched and current_field is not None and stripped:
            current_lines.append(stripped)

    if current_field is not None:
        result[current_field] = '\n'.join(current_lines).strip()

    return result


def write_p1_to_sheet(poster_name, p1_data):
    """P1事例をスプレッドシートの「P1報告内容」タブに保存する"""
    try:
        creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
        if not creds_json or not P1_SPREADSHEET_ID:
            return False
        creds_dict = json.loads(creds_json)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(P1_SPREADSHEET_ID)

        try:
            p1_sheet = spreadsheet.worksheet("P1報告内容")
        except Exception:
            p1_sheet = spreadsheet.add_worksheet(title="P1報告内容", rows=200, cols=len(P1_HEADERS))

        all_rows = p1_sheet.get_all_values()
        if not all_rows:
            p1_sheet.append_row(P1_HEADERS)
            all_rows = [P1_HEADERS]

        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

        def clean(val):
            return val.replace('\n', ' ').strip() if val else ''

        row = [
            now_str,
            poster_name,
            clean(p1_data.get('date', '')),
            clean(p1_data.get('project', '')),
            clean(p1_data.get('summary', '')),
            clean(p1_data.get('content', '')),
            clean(p1_data.get('cause', '')),
            clean(p1_data.get('client', '')),
            clean(p1_data.get('partners', '')),
            clean(p1_data.get('internal', '')),
        ]
        p1_sheet.append_row(row, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        print("P1 sheet write error:", e)
        return str(e)


def write_p1_action_items(p1_data, poster_name):
    """P1報告の改善策を「改善策管理」シートに追加する"""
    try:
        creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
        if not creds_json or not P1_SPREADSHEET_ID:
            return
        creds_dict = json.loads(creds_json)
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(P1_SPREADSHEET_ID)

        try:
            sheet = spreadsheet.worksheet("改善策管理")
        except Exception:
            sheet = spreadsheet.add_worksheet(title="改善策管理", rows=500, cols=9)

        headers = ['記録日', '投稿者', '案件名', '概要', '種別', '改善策内容', '状況', '確認日', '備考']
        if not sheet.row_values(1):
            sheet.append_row(headers)

        now_str = datetime.now(JST).strftime("%Y-%m-%d")
        project = p1_data.get('project', '')
        summary = p1_data.get('summary', '')

        actions = [
            ('クライアント対応策', p1_data.get('client', '')),
            ('関係者対応策',       p1_data.get('partners', '')),
            ('社内改善策',         p1_data.get('internal', '')),
        ]
        for kind, content in actions:
            if content and content not in ('なし', 'なし。', '-', ''):
                row = [now_str, poster_name, project, summary, kind, content, '未実施', '', '']
                sheet.append_row(row, value_input_option='USER_ENTERED')
    except Exception as e:
        print("Action items write error:", e)


def update_p1_monthly(year_month=None):
    """P1報告を月次で集計・カテゴリ分類してシートに書き込む"""
    try:
        creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
        if not creds_json or not P1_SPREADSHEET_ID:
            return
        creds_dict = json.loads(creds_json)
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(P1_SPREADSHEET_ID)

        # P1報告内容シートを読む
        try:
            p1_sheet = spreadsheet.worksheet("P1報告内容")
        except Exception:
            return
        all_rows = p1_sheet.get_all_values()
        if len(all_rows) <= 1:
            return

        if not year_month:
            year_month = datetime.now(JST).strftime("%Y-%m")

        # 対象月のデータ抽出
        # 列: 記録日時,投稿者,①日時,②案件名,③概要,④内容,⑤原因,⑥クライアント,⑦関係者,⑧社内
        cases = []
        for row in all_rows[1:]:
            if len(row) >= 1 and row[0].startswith(year_month):
                cases.append({
                    'project': row[3] if len(row) > 3 else '',
                    'summary': row[4] if len(row) > 4 else '',
                    'content': row[5] if len(row) > 5 else '',
                    'cause':   row[6] if len(row) > 6 else '',
                    'client':  row[7] if len(row) > 7 else '',
                    'partners':row[8] if len(row) > 8 else '',
                    'internal':row[9] if len(row) > 9 else '',
                })

        if not cases:
            return

        # Geminiで分析
        cases_text = "\n---\n".join([
            f"案件名：{c['project']}\n概要：{c['summary']}\n原因：{c['cause']}\n社内改善策：{c['internal']}"
            for c in cases
        ])
        analysis_prompt = (
            f"以下は{year_month}の株式会社L&BのP1報告（トラブル・問題事例）です。\n\n"
            f"{cases_text}\n\n"
            f"以下の形式で分析してください：\n"
            f"【カテゴリー分類】各案件をカテゴリー（例：工程管理/顧客対応/施工品質/社内連携/書類・申請 など）に分類\n"
            f"【原因パターン】共通して見られる原因の傾向を2〜3点\n"
            f"【重点改善アクション】最も優先すべき改善策を2〜3点\n"
            f"【社長へのコメント】全体を踏まえた七種社長への一言\n"
            f"簡潔に、箇条書きで。"
        )
        analysis = gemini_generate(analysis_prompt) or "（AI分析取得できませんでした）"

        # P1月次集計シート
        try:
            monthly = spreadsheet.worksheet("P1月次集計")
        except Exception:
            monthly = spreadsheet.add_worksheet(title="P1月次集計", rows=200, cols=6)

        m_headers = ['年月', '件数', 'カテゴリー分類', '原因パターン・重点改善アクション', '社長へのコメント', '更新日時']
        if not monthly.row_values(1):
            monthly.append_row(m_headers)

        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
        new_row = [year_month, len(cases), analysis, '', '', now_str]

        # 既存行を探して上書き or 追加
        all_monthly = monthly.get_all_values()
        updated = False
        for i, row in enumerate(all_monthly[1:], 2):
            if row and row[0] == year_month:
                monthly.update(f'A{i}:F{i}', [new_row])
                updated = True
                break
        if not updated:
            monthly.append_row(new_row, value_input_option='USER_ENTERED')

    except Exception as e:
        print("P1 monthly summary error:", e)


# ============================================================
# エンドポイント
# ============================================================

@app.route("/", methods=['GET'])
def health_check():
    return 'OK'


@app.route("/morning_summary", methods=['GET', 'POST'])
def morning_summary():
    """外部cronから朝9時15分に呼び出す"""
    send_morning_greeting_to_groups()
    summary = build_morning_summary()
    if NANA_LINE_USER_ID:
        push_message(NANA_LINE_USER_ID, summary)
    return summary


@app.route("/evening_summary", methods=['GET', 'POST'])
def evening_summary():
    """外部cronから夜9時に呼び出す"""
    summary = build_evening_summary()
    if NANA_LINE_USER_ID:
        push_message(NANA_LINE_USER_ID, summary)
    send_encouraging_messages()
    return summary


@app.route("/p1_monthly", methods=['GET', 'POST'])
def p1_monthly():
    """月次P1集計：cron-job.orgから毎月1日に呼び出す"""
    year_month = request.args.get('month') or datetime.now(JST).strftime("%Y-%m")
    update_p1_monthly(year_month)
    msg = f"📊 {year_month}のP1月次集計を更新しました。"
    if NANA_LINE_USER_ID:
        push_message(NANA_LINE_USER_ID, msg)
    return msg


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
        if event['type'] != 'message' or event['message']['type'] != 'text':
            continue

        user_message = event['message']['text']
        reply_token = event['replyToken']
        user_id = event['source']['userId']
        source_type = event['source'].get('type', 'user')
        group_id = event['source'].get('groupId') or event['source'].get('roomId')
        timestamp = event.get('timestamp', 0)

        # ====================================================
        # グループチャット：勤怠報告の処理
        # ====================================================
        if source_type in ('group', 'room') and group_id:
            # グループIDを保存
            group_ids = redis_get("group_ids") or []
            if not isinstance(group_ids, list):
                group_ids = []
            if group_id not in group_ids:
                group_ids.append(group_id)
                redis_set("group_ids", group_ids)

            name = redis_get(f"staff:{user_id}:name")
            if not name:
                name = get_line_profile_name(user_id, group_id)
                redis_set(f"staff:{user_id}:name", name)

            is_structured_p1 = '①日時' in user_message and '②案件名' in user_message
            is_freeform_p1 = re.search(r'【.{1,30}(報告|トラブル|問題|不具合|クレーム|遅延|事故)】', user_message)

            if is_structured_p1 or is_freeform_p1:
                # 構造化 or 自由形式でパース
                if is_structured_p1:
                    p1_data = parse_p1_report(user_message)
                else:
                    # 自由形式：Geminiで構造化
                    parse_prompt = (
                        f"以下の報告メッセージを読んで、JSON形式で情報を抽出してください。\n"
                        f"キー：date（日付）, project（案件名）, summary（概要1行）, content（内容詳細）, cause（原因）, client（クライアント対応）, partners（関係者対応）, internal（社内対応）\n"
                        f"不明な項目は空文字にしてください。必ずJSONのみ返してください。\n\n"
                        f"報告：\n{user_message}"
                    )
                    parsed_json = gemini_generate(parse_prompt) or '{}'
                    try:
                        parsed_json = re.sub(r'```(?:json)?\n?', '', parsed_json).strip('`').strip()
                        p1_data = json.loads(parsed_json)
                    except Exception:
                        p1_data = {
                            'date': today_jst(),
                            'project': re.search(r'【(.+?)】', user_message).group(1) if re.search(r'【(.+?)】', user_message) else '',
                            'summary': user_message[:50],
                            'content': user_message,
                            'cause': '', 'client': '', 'partners': '', 'internal': ''
                        }

                # スプレッドシート書き込み
                result = write_p1_to_sheet(name, p1_data)
                write_p1_action_items(p1_data, name)

                # Geminiで感謝メッセージを生成してpushで送信
                praise_angles = [
                    "問題に真剣に向き合った誠実さと勇気",
                    "失敗を隠さず共有してくれた誠実さ",
                    "改善策まで考えて報告してくれた責任感",
                    "チーム全体のために声を上げた勇気",
                    "この報告が会社の財産になるという感謝",
                    "困難を乗り越えようとするプロ意識",
                    "正直な報告が信頼を生むという価値",
                ]
                day_index = datetime.now(JST).day % len(praise_angles)
                focus = praise_angles[day_index]
                p1_prompt = (
                    f"あなたはエリザベスです。株式会社L&Bの専属AIアシスタント秘書です。\n"
                    f"{name}さんが「{p1_data.get('summary','問題事例')}」というP1報告を提出してくれました。\n"
                    f"今日特に伝えたいこと：{focus}\n"
                    f"報告への感謝と勇気を称える言葉を2〜3文で。温かく誠実なトーンで。"
                )
                fallbacks = [
                    f"{name}さん、正直なご報告をありがとうございます。この勇気ある共有が会社を強くします。",
                    f"{name}さん、貴重なP1報告に心から感謝します。問題に向き合う姿勢が素晴らしいです。",
                    f"{name}さん、ありがとうございます。この報告が必ずチーム全体の学びになります。",
                    f"{name}さん、正直に共有してくださり、本当にありがとうございます。",
                    f"{name}さん、改善策まで考えてくださった誠実さに感謝します。",
                ]
                fallback = fallbacks[datetime.now(JST).day % len(fallbacks)]
                p1_reply = gemini_generate(p1_prompt) or fallback
                reply_message(reply_token, f"📋 {p1_reply}")
                continue

            if '【本日の業務】' in user_message:
                report_data = parse_morning_report(user_message)
                save_morning_report(user_id, name, report_data, timestamp)
                reply_message(reply_token,
                    f"✅ {name}さん、朝のご報告ありがとうございます！\n今日も一日頑張りましょう💪")
                continue

            if '【日報】' in user_message:
                report_data = parse_evening_report(user_message)
                save_evening_report(user_id, name, report_data, timestamp)

                praise_angles = [
                    "今日完了したタスクへの取り組み姿勢や丁寧さ",
                    "体調管理・コンディションへの意識の高さ",
                    "報告の誠実さや情報共有の姿勢",
                    "仕事への粘り強さ・継続力",
                    "チームへの貢献や周囲への気配り",
                    "日々の積み重ねと成長",
                    "プロとしての仕事への誠実な向き合い方",
                ]
                day_index = datetime.now(JST).day % len(praise_angles)
                focus = praise_angles[day_index]

                tasks = report_data.get("completed_tasks", [])
                health = report_data.get("health_score", "")
                checkout = report_data.get("checkout_time", "")
                context = f"完了タスク：{', '.join(tasks)}\n体調：{health}点\n退社：{checkout}"

                pm_prompt = (
                    f"あなたはエリザベスです。株式会社L&Bの専属AIアシスタント秘書です。\n"
                    f"社長・七種珠水の言葉として、{name}さんへのメッセージを届けます。\n\n"
                    f"【{name}さんの今日の報告内容】\n{context}\n\n"
                    f"【今日特に注目するポイント】{focus}\n\n"
                    f"以下を意識してメッセージを作成してください：\n"
                    f"・報告の『中身』を具体的に読み込んで言及する（タスク名・体調・内容に直接触れる）\n"
                    f"・数字や具体的な行動に感動・共感を示す\n"
                    f"・{name}さんのプロ意識や人間性を七種社長として心から称える\n"
                    f"・「報告してくれてありがとう」ではなく「あなたの仕事ぶりに感動した」という視点で\n"
                    f"・末尾に「七種より」と添える\n\n"
                    f"3〜4文。心に響く、温かく力強いトーンで。"
                )
                fallbacks = [
                    f"{name}さん、今日も一日お疲れ様でした！丁寧な日報をありがとうございます🌙",
                    f"{name}さん、お疲れ様でした！今日の頑張りがきっと明日につながります✨",
                    f"{name}さん、日報ありがとうございます。今日も誠実に仕事に向き合いましたね🌙",
                    f"{name}さん、お疲れ様でした！毎日の積み重ねが力になっています💪",
                    f"{name}さん、今日もありがとうございました。ゆっくり休んでください🌙",
                ]
                fallback = fallbacks[datetime.now(JST).day % len(fallbacks)]
                pm_reply = gemini_generate(pm_prompt) or fallback
                reply_message(reply_token, f"✅ {pm_reply}")
                continue

            # その他のグループメッセージには返答しない
            continue

        # ====================================================
        # 1対1チャット：ナナさんとの会話
        # ====================================================

        # LINE User IDを返すコマンド
        if 'ID' in user_message and ('教えて' in user_message or '登録' in user_message):
            reply_message(reply_token,
                f"あなたのLINE User IDは以下です：\n\n{user_id}\n\nこれをRenderの環境変数 NANA_LINE_USER_ID に設定してください。")
            continue

        # 勤怠サマリーをナナさんが手動で確認するコマンド
        if '今日の報告' in user_message or '勤怠確認' in user_message:
            summary = build_morning_summary() + "\n\n" + build_evening_summary()
            reply_message(reply_token, summary)
            continue

        # 通常の会話（スケジュール管理含む）
        history = redis_get_conv(user_id)
        history.append({"role": "user", "parts": [{"text": user_message}]})
        if len(history) > 20:
            history = history[-20:]

        try:
            gemini_json = gemini_chat(history)
            if 'candidates' not in gemini_json:
                reply_text = "エラー: " + str(gemini_json.get('error', {}).get('message', str(gemini_json)))
            else:
                raw_text = gemini_json['candidates'][0]['content']['parts'][0]['text']
                schedules = redis_get_schedules(user_id)

                schedule_matches = re.findall(r'\[\[SCHEDULE:(\{.*?\})\]\]', raw_text)
                for match in schedule_matches:
                    try:
                        entry = json.loads(match)
                        schedules.append(entry)
                    except Exception:
                        pass
                if schedule_matches:
                    redis_set_schedules(user_id, schedules)

                show_schedule = '[[SHOW_SCHEDULE]]' in raw_text

                delete_matches = re.findall(r'\[\[DELETE_SCHEDULE:(\d+)\]\]', raw_text)
                for num in delete_matches:
                    idx = int(num) - 1
                    if 0 <= idx < len(schedules):
                        schedules.pop(idx)
                if delete_matches:
                    redis_set_schedules(user_id, schedules)

                clean_text = re.sub(r'\[\[SCHEDULE:\{.*?\}\]\]', '', raw_text)
                clean_text = clean_text.replace('[[SHOW_SCHEDULE]]', '')
                clean_text = re.sub(r'\[\[DELETE_SCHEDULE:\d+\]\]', '', clean_text)
                clean_text = clean_text.strip()

                if show_schedule:
                    reply_text = format_schedules(schedules)
                elif schedule_matches:
                    reply_text = clean_text + "\n\n✅ 予定を登録しました。"
                else:
                    reply_text = clean_text

                history.append({"role": "model", "parts": [{"text": raw_text}]})
                redis_set_conv(user_id, history)

        except Exception as e:
            reply_text = "例外エラー: " + str(e)

        reply_message(reply_token, reply_text)

    return 'OK'


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
