"""
ゴミ分類を判定するモジュール。

処理の流れ:
1. CSV完全一致・高確信度一致 → 分類＋ルールベースの捨て方を返す
2. CSV部分一致候補 → 候補リストを返す（複数の場合）
3. OpenAI フォールバック → AIで分類を推測して返す
※ PDF参考情報の付加は main.py 側で行う
"""

import os
import base64
from dataclasses import dataclass
from openai import OpenAI
from dotenv import load_dotenv
from app.data_loader import load_garbage_data, get_exact_match, search_item, get_candidates, get_loose_candidates
from app.disposal_rules import DISPOSAL_RULES, get_item_note
from app.pdf_searcher import search_pdfs, render_page_png

# .envファイルからAPIキーを読み込む
load_dotenv()

# OpenAIクライアントの初期化（APIキーは.envから自動で読まれる）
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# アプリ起動時に一度だけCSVを読み込む（毎回読むと遅いため）
ITEMS_TO_CATEGORY, CATEGORY_DEFINITIONS = load_garbage_data()

# システムプロンプトをファイルから読み込む
_prompt_path = os.path.join(os.path.dirname(__file__), '..', 'Input', 'gomi_bot_prompt_answer.txt')
with open(_prompt_path, encoding='utf-8') as f:
    SYSTEM_PROMPT = f.read()

@dataclass
class CandidatesResult:
    """CSV検索で複数の候補が見つかった場合の結果。"""
    query: str
    candidates: list[tuple[str, str]]  # [(品目名, 分類名), ...]

    def format_message(self) -> str:
        lines = [f'「{self.query}」に近い品目が複数あります。当てはまるものを番号で教えてください：']
        for i, (item, category) in enumerate(self.candidates, 1):
            lines.append(f'{i}. {item}（{category}）')
        lines.append(f'{len(self.candidates) + 1}. 上記以外・わからない')
        return '\n'.join(lines)


def _format_response(item: str, category: str) -> str:
    """分類名と捨て方ルールを組み合わせてLINE返信メッセージを組み立てる。"""
    rule = DISPOSAL_RULES.get(category)

    msg = f'「{item}」\n【分類】{category}'

    if rule:
        msg += f'\n【捨て方】{rule["捨て方"]}'
        # カテゴリ共通の注意事項
        if rule.get('注意事項'):
            msg += f'\n【注意事項】{rule["注意事項"]}'
    else:
        msg += '\n詳しい捨て方は安城市のウェブサイトをご確認ください。'

    # 品目固有の注意事項（追加で存在する場合のみ）
    item_note = get_item_note(item)
    if item_note:
        msg += f'\n【この品目の注意】{item_note}'

    return msg


@dataclass
class AIConversationResult:
    """OpenAIが質問を返してきた場合の結果。会話継続用。"""
    response: str
    messages: list[dict]


def _call_openai(messages: list[dict]) -> str:
    """OpenAI APIを呼び出してテキスト回答を返す。"""
    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=messages,
        max_tokens=300,
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def _is_question(text: str) -> bool:
    return '？' in text or '?' in text


def _build_csv_context(related_items: list[tuple[str, str]]) -> str:
    """OpenAIに渡すCSVコンテキストを組み立てる。"""
    lines = ['【安城市の分類カテゴリと捨て方】']
    for cat, rule in DISPOSAL_RULES.items():
        lines.append(f'- {cat}: {rule["捨て方"]}')
    if related_items:
        lines.append('\n【CSVに登録されている類似品目（参考）】')
        for ri_item, ri_cat in related_items:
            lines.append(f'- {ri_item} → {ri_cat}')
    return '\n'.join(lines)


def _build_user_content(item: str) -> list[dict] | str:
    """ユーザーメッセージを組み立てる。PDFにヒットがあれば画像も含める。"""
    pdf_matches = search_pdfs(item)
    if not pdf_matches:
        return item

    prompt_text = (
        f'「{item}」の捨て方を教えてください。\n'
        '以下は安城市の公式PDF（分け方・出し方ガイド）のページ画像です。'
        '画像に該当する情報が記載されている場合は、その内容を優先して分類・回答してください。'
    )
    content: list[dict] = [{'type': 'text', 'text': prompt_text}]
    for pdf_name, page_num, display_name in pdf_matches:
        try:
            png_bytes = render_page_png(pdf_name, page_num, item)
            b64 = base64.b64encode(png_bytes).decode('utf-8')
            content.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/png;base64,{b64}', 'detail': 'high'},
            })
        except Exception:
            pass

    return content if len(content) > 1 else item


def ask_openai(item: str, related_items: list[tuple[str, str]] | None = None) -> str | AIConversationResult:
    """OpenAI APIに品目を問い合わせる。質問が返ってきた場合はAIConversationResultを返す。"""
    context = _build_csv_context(related_items or [])
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT + '\n\n' + context},
        {'role': 'user', 'content': _build_user_content(item)},
    ]
    response_text = _call_openai(messages)
    if _is_question(response_text):
        messages.append({'role': 'assistant', 'content': response_text})
        return AIConversationResult(response=response_text, messages=messages)
    return response_text + '\n\n※ChatGPTによる回答'


def continue_ai_conversation(messages: list[dict], user_reply: str) -> str | AIConversationResult:
    """進行中のAI会話にユーザーの返答を追加して続きを取得する。"""
    messages = messages + [{'role': 'user', 'content': user_reply}]
    response_text = _call_openai(messages)
    if _is_question(response_text):
        messages.append({'role': 'assistant', 'content': response_text})
        return AIConversationResult(response=response_text, messages=messages)
    return response_text + '\n\n※ChatGPTによる回答'


def classify_selection(result: CandidatesResult, choice: int) -> str | AIConversationResult:
    """
    候補リストからユーザーが選んだ番号を処理して返信メッセージを返す。

    引数:
        result: CandidatesResult（候補リストと元クエリ）
        choice: ユーザーが入力した番号（1始まり）

    戻り値:
        LINEに返すメッセージ文字列
    """
    if 1 <= choice <= len(result.candidates):
        item, category = result.candidates[choice - 1]
        return _format_response(item, category)
    if choice == len(result.candidates) + 1:
        loose = get_loose_candidates(result.query, ITEMS_TO_CATEGORY)
        return ask_openai(result.query, loose)
    return f'1〜{len(result.candidates) + 1}の番号で選んでください。'


def classify(item: str) -> str | CandidatesResult | AIConversationResult:
    """
    品目名を受け取り、分類結果のメッセージまたは候補リストを返す。

    引数:
        item: ユーザーが入力した品目名（例: "ペットボトル"）

    戻り値:
        str: 完全一致で分類が確定した場合のLINE返信メッセージ
        CandidatesResult: 候補がある場合（main.pyでセッション管理）
        AIConversationResult: OpenAIが質問を返してきた場合（main.pyで会話管理）
    ※ PDF参考情報の付加は main.py 側で行う
    """
    # ① 完全一致のみ自動確定
    exact = get_exact_match(item, ITEMS_TO_CATEGORY)
    if exact:
        category, matched_item = exact
        return _format_response(matched_item, category)

    # ② 高確信度一致・部分一致・緩い一致をすべて候補リストにまとめる
    candidates: list[tuple[str, str]] = []

    # search_item の高確信度一致を先頭候補として追加
    high = search_item(item, ITEMS_TO_CATEGORY)
    if high:
        category, matched_item = high
        candidates.append((matched_item, category))

    # 部分一致候補を追加（重複除去）
    seen_items = {c[0] for c in candidates}
    for c in get_candidates(item, ITEMS_TO_CATEGORY):
        if c[0] not in seen_items:
            candidates.append(c)
            seen_items.add(c[0])

    if candidates:
        return CandidatesResult(query=item, candidates=candidates)

    # ③ 緩い一致候補
    loose = get_loose_candidates(item, ITEMS_TO_CATEGORY)
    if loose:
        return CandidatesResult(query=item, candidates=loose)

    # ④ 候補が全くない場合のみOpenAIにフォールバック
    return ask_openai(item)


# --- 動作確認用 ---
if __name__ == '__main__':
    test_items = [
        'ペットボトル',      # CSV にある（拠点回収品目）
        'スマートフォン',    # CSV にある
        '電子レンジ',        # CSV にある（粗大ごみ）
        'スプレー缶',        # CSV にある（資源粉砕困難）
        'マスク',            # CSV にない → OpenAI
        '壊れたドライヤー',  # CSV にない → OpenAI
    ]

    for item in test_items:
        print(f'=== 入力: {item} ===')
        result = classify(item)
        print(result)
        print()
