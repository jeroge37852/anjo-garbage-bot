"""
ゴミ分類を判定するモジュール。

処理の流れ:
1. CSV完全一致・高確信度一致 → 分類＋ルールベースの捨て方を返す
2. CSV部分一致候補 → 候補リストを返す（複数の場合）
3. OpenAI フォールバック → AIで分類を推測して返す
※ PDF参考情報の付加は main.py 側で行う
"""

import os
from dataclasses import dataclass
from openai import OpenAI
from dotenv import load_dotenv
from app.data_loader import load_garbage_data, search_item, get_candidates
from app.disposal_rules import DISPOSAL_RULES, get_item_note

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

    # OpenAI の生回答を追記
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': item},
    ]
    ai_raw = _call_openai(messages)
    msg += f'\n\n---\n【OpenAI 生回答】\n{ai_raw}'

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


def ask_openai(item: str) -> str | AIConversationResult:
    """OpenAI APIに品目を問い合わせる。質問が返ってきた場合はAIConversationResultを返す。"""
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': item},
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
        return ask_openai(result.query)
    return f'1〜{len(result.candidates) + 1}の番号で選んでください。'


def classify(item: str) -> str | CandidatesResult | AIConversationResult:
    """
    品目名を受け取り、分類結果のメッセージまたは候補リストを返す。

    引数:
        item: ユーザーが入力した品目名（例: "ペットボトル"）

    戻り値:
        str: 分類が確定した場合のLINE返信メッセージ
        CandidatesResult: 候補が複数ある場合（main.pyでセッション管理）
        AIConversationResult: OpenAIが質問を返してきた場合（main.pyで会話管理）
    ※ PDF参考情報の付加は main.py 側で行う
    """
    # ① CSV完全一致・高確信度一致
    result = search_item(item, ITEMS_TO_CATEGORY)
    if result:
        category, matched_item = result
        return _format_response(matched_item, category)

    # ② 部分一致候補を収集
    candidates = get_candidates(item, ITEMS_TO_CATEGORY)
    if len(candidates) >= 2:
        return CandidatesResult(query=item, candidates=candidates)
    if len(candidates) == 1:
        matched_item, category = candidates[0]
        return _format_response(matched_item, category)

    # ③ OpenAI にフォールバック
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
