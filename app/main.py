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
    ImageMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from dotenv import load_dotenv

from app.classifier import classify, classify_selection, continue_ai_conversation, CandidatesResult, AIConversationResult, PDFPageResult
from app.pdf_searcher import render_page_png

# .env を読み込む
load_dotenv()

LINE_CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
BASE_URL = os.environ.get('BASE_URL', '').rstrip('/')

app = Flask(__name__)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# LINE Messaging API クライアント
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# ユーザーごとの候補選択セッション（再起動でリセットされる）
_pending: dict[str, CandidatesResult] = {}
# ユーザーごとのOpenAI会話履歴（AIが質問を返した場合に保持）
_ai_session: dict[str, list[dict]] = {}


def _build_candidates_message(result: CandidatesResult) -> TextMessage:
    """候補リストをQuick Replyボタン付きメッセージとして組み立てる。"""
    items = []
    for i, (item, _category) in enumerate(result.candidates, 1):
        label = item if len(item) <= 20 else item[:19] + '…'
        items.append(QuickReplyItem(action=MessageAction(label=label, text=str(i))))
    last = len(result.candidates) + 1
    items.append(QuickReplyItem(action=MessageAction(label='その他・わからない', text=str(last))))

    return TextMessage(
        text=f'「{result.query}」に近い品目が複数あります。当てはまるものを選んでください：',
        quick_reply=QuickReply(items=items),
    )


@app.route('/', methods=['GET'])
def health_check():
    """Render のヘルスチェック・動作確認用"""
    return 'OK', 200


@app.route('/pdf-page/<pdf_name>/<int:page_num>', methods=['GET'])
def serve_pdf_page(pdf_name: str, page_num: int):
    """PDFの指定ページをPNG画像として返す（LINEの ImageMessage 用）"""
    try:
        png_bytes = render_page_png(pdf_name, page_num)
        return png_bytes, 200, {'Content-Type': 'image/png'}
    except Exception:
        abort(404)


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

    reply_messages = []

    if user_id in _ai_session:
        # OpenAIとの会話が進行中 → 続きを取得
        messages = _ai_session.pop(user_id)
        result = continue_ai_conversation(messages, user_text)
        if isinstance(result, AIConversationResult):
            _ai_session[user_id] = result.messages
            reply_messages = [TextMessage(text=result.response)]
        else:
            reply_messages = [TextMessage(text=result)]
    elif user_id in _pending:
        pending = _pending[user_id]
        if user_text.isdigit() and 1 <= int(user_text) <= len(pending.candidates) + 1:
            # 有効な番号 → 選択を処理してセッション終了
            _pending.pop(user_id)
            result = classify_selection(pending, int(user_text))
            if isinstance(result, AIConversationResult):
                _ai_session[user_id] = result.messages
                reply_messages = [TextMessage(text=result.response)]
            else:
                reply_messages = [TextMessage(text=result)]
        else:
            # 番号以外 → ボタンを再表示してセッション維持
            reply_messages = [_build_candidates_message(pending)]
    else:
        result = classify(user_text)
        if isinstance(result, CandidatesResult):
            _pending[user_id] = result
            reply_messages = [_build_candidates_message(result)]
        elif isinstance(result, AIConversationResult):
            _ai_session[user_id] = result.messages
            reply_messages = [TextMessage(text=result.response)]
        elif isinstance(result, PDFPageResult):
            for pdf_name, page_num, display_name in result.matches:
                reply_messages.append(
                    TextMessage(text=f'「{result.item}」が {display_name} の {page_num} ページに記載されています。')
                )
                if BASE_URL:
                    url = f'{BASE_URL}/pdf-page/{pdf_name}/{page_num}'
                    reply_messages.append(ImageMessage(original_content_url=url, preview_image_url=url))
        else:
            reply_messages = [TextMessage(text=result)]

    # LINE に返信
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=reply_messages,
            )
        )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
