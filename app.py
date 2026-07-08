"""
Chatwork AIディレクターマネジメントボット
- Webhookでメンションを受け取り、OpenAI GPT-4o-miniで回答を生成してChatworkに返信する
- 対象ルームの過去メッセージを参照してコンテキストを提供する
"""

import os
import sys
import json
import threading
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

# ログをフラッシュする設定
sys.stdout.reconfigure(line_buffering=True)

app = Flask(__name__)

# 設定（環境変数から読み込み）
CHATWORK_API_TOKEN = os.environ.get("CHATWORK_API_TOKEN", "")
ROOM_ID = os.environ.get("CHATWORK_ROOM_ID", "343258579")
BOT_ACCOUNT_ID = int(os.environ.get("BOT_ACCOUNT_ID", "10506675"))

# OpenAI クライアント
ai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

# ルームの説明文（システムプロンプト）
ROOM_DESCRIPTION = """
あなたは「ショート動画屋さん_ディレクター」ルームのAIアシスタントです。
このルームは、ショート動画制作会社「株式会社美手紙」のディレクターチームが使用するチャットルームです。

ルームの概要:
- ショート動画のディレクターと運営チームが連絡・相談を行う場所
- ディレクターのマネジメント、案件管理、品質確認などが主なトピック
- メンバー間の連絡調整、タスク管理、進捗確認なども行われる

あなたの役割:
- ディレクターからの質問や相談に対して、的確かつ丁寧に回答する
- 過去のチャット内容を参考に、文脈を理解した上で回答する
- ショート動画制作の現場に即した実用的なアドバイスを提供する
- 返信は日本語で、丁寧かつ簡潔に行う
- Chatworkのマークアップ記法（[info][title]タイトル[/title]内容[/info] など）を適切に使用する
"""

def log(msg):
    """ログ出力"""
    print(msg, flush=True)

def get_recent_messages(limit=30):
    """ルームの最近のメッセージを取得する"""
    try:
        url = f"https://api.chatwork.com/v2/rooms/{ROOM_ID}/messages"
        headers = {"X-ChatWorkToken": CHATWORK_API_TOKEN}
        params = {"force": 1}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            messages = response.json()
            recent = messages[-limit:] if len(messages) > limit else messages
            return recent
        log(f"メッセージ取得失敗: {response.status_code}")
        return []
    except Exception as e:
        log(f"メッセージ取得エラー: {e}")
        return []

def format_messages_for_context(messages):
    """メッセージをAIのコンテキスト用にフォーマットする"""
    formatted = []
    for msg in messages:
        sender = msg.get("account", {}).get("name", "不明")
        body = msg.get("body", "")
        if len(body) > 300:
            body = body[:300] + "..."
        formatted.append(f"[{sender}]: {body}")
    return "\n".join(formatted)

def send_chatwork_message(room_id, message, reply_to_account_id=None):
    """Chatworkにメッセージを送信する"""
    try:
        url = f"https://api.chatwork.com/v2/rooms/{room_id}/messages"
        headers = {
            "X-ChatWorkToken": CHATWORK_API_TOKEN,
            "Content-Type": "application/x-www-form-urlencoded"
        }
        if reply_to_account_id:
            message = f"[To:{reply_to_account_id}]\n{message}"
        data = {"body": message}
        response = requests.post(url, headers=headers, data=data, timeout=10)
        log(f"送信結果: {response.status_code} - {response.text[:100]}")
        return response.status_code == 200
    except Exception as e:
        log(f"メッセージ送信エラー: {e}")
        return False

def generate_ai_response(question, context_messages):
    """OpenAI GPT-4o-miniを使ってAI回答を生成する"""
    try:
        context_text = format_messages_for_context(context_messages)

        messages = [
            {
                "role": "system",
                "content": ROOM_DESCRIPTION + f"\n\n【最近のチャット履歴（参考）】\n{context_text}"
            },
            {
                "role": "user",
                "content": question
            }
        ]

        log("OpenAI API呼び出し中...")
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=800,
        )

        result = response.choices[0].message.content
        log(f"AI回答生成完了: {result[:100]}")
        return result

    except Exception as e:
        log(f"AI回答生成エラー: {e}")
        import traceback
        log(traceback.format_exc())
        return f"申し訳ありません、回答の生成中にエラーが発生しました。\n（エラー: {str(e)}）"

def process_webhook_async(event_data):
    """Webhookイベントを非同期で処理する（10秒制限対策）"""
    try:
        event_type = event_data.get("webhook_event_type", "")
        event = event_data.get("webhook_event", {})

        log(f"[THREAD] イベントタイプ: {event_type}")
        log(f"[THREAD] イベント内容: {json.dumps(event, ensure_ascii=False)}")

        if event_type in ["mention_to_me", "message_created"]:
            room_id = str(event.get("room_id", ""))
            message_body = event.get("body", "")
            from_account_id = event.get("from_account_id") or event.get("account_id")

            if from_account_id == BOT_ACCOUNT_ID:
                log("[THREAD] ボット自身のメッセージのためスキップ")
                return

            if room_id != ROOM_ID:
                log(f"[THREAD] 対象外ルーム: {room_id}")
                return

            if event_type == "message_created":
                if f"[To:{BOT_ACCOUNT_ID}]" not in message_body:
                    log("[THREAD] ボットへのメンションなし、スキップ")
                    return

            question = message_body.replace(f"[To:{BOT_ACCOUNT_ID}]", "").strip()
            log(f"[THREAD] 質問: {question}")

            recent_messages = get_recent_messages(limit=30)
            log(f"[THREAD] コンテキストメッセージ取得: {len(recent_messages)}件")

            ai_response = generate_ai_response(question, recent_messages)

            success = send_chatwork_message(room_id, ai_response, reply_to_account_id=from_account_id)
            log(f"[THREAD] Chatwork返信: {'成功' if success else '失敗'}")

    except Exception as e:
        log(f"[THREAD] Webhook処理エラー: {e}")
        import traceback
        log(traceback.format_exc())

@app.route("/webhook", methods=["POST"])
def webhook():
    """ChatworkのWebhookエンドポイント"""
    try:
        data = request.get_json(force=True)
        log(f"Webhook受信: {json.dumps(data, ensure_ascii=False)[:200]}")

        thread = threading.Thread(target=process_webhook_async, args=(data,))
        thread.daemon = True
        thread.start()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        log(f"Webhookエンドポイントエラー: {e}")
        return jsonify({"status": "error", "message": str(e)}), 200

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "Chatwork AI Bot is running"}), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "bot": "Chatwork AI Bot", "room_id": ROOM_ID}), 200

@app.route("/test", methods=["GET"])
def test():
    try:
        url = f"https://api.chatwork.com/v2/rooms/{ROOM_ID}"
        headers = {"X-ChatWorkToken": CHATWORK_API_TOKEN}
        response = requests.get(url, headers=headers, timeout=10)
        return jsonify(response.json()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log("=== Chatwork AI ディレクターマネジメントボット 起動中 ===")
    log(f"対象ルームID: {ROOM_ID}")
    log(f"ボットアカウントID: {BOT_ACCOUNT_ID}")
    app.run(host="0.0.0.0", port=port, debug=False)
