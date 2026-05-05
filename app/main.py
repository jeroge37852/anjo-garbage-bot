"""
LINE Bot Webhook サーバー（Flask）

エンドポイント:
  GET  /         → 動作確認用（Renderのヘルスチェックにも使用）
  POST /callback → LINE Platform からの Webhook を受け取る
"""

import os
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from dotenv import load_dotenv

from app.classifier import classify, classify_selection, CandidatesResult

# .env を読み込む
load_dotenv()

LINE_CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']

app = Flask(__name__)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# LINE Messaging API クライアント
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# ユーザーごとの候補選択セッション（再起動でリセットされる）
_pending: dict[str, CandidatesResult] = {}


@app.route('/', methods=['GET'])
def health_check():
    """Render のヘルスチェック・動作確認用"""
    return 'OK', 200


@app.route('/callback', methods=['POST'])
def callback():
    """LINE Platform からの Webhook を受け取るエンドポイント"""
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK', 200


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    """ユーザーがテキストを送ってきたときの処理"""
    user_text = event.message.text.strip()

    # 空メッセージは無視
    if not user_text:
        return

    user_id = event.source.user_id

    # 候補選択中のユーザーが番号を送ってきた場合
    if user_id in _pending and user_text.isdigit():
        pending = _pending.pop(user_id)
        reply_text = classify_selection(pending, int(user_text))
    else:
        # 番号以外の入力は新しいクエリとして処理（pending があればクリア）
        _pending.pop(user_id, None)
        result = classify(user_text)
        if isinstance(result, CandidatesResult):
            _pending[user_id] = result
            reply_text = result.format_message()
        else:
            reply_text = result

    # LINE に返信
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
