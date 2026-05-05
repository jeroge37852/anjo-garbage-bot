"""
ゴミ分類を判定するモジュール。

処理の流れ:
1. 素材キーワードが含まれる場合 → OpenAI APIで分類を推測（CSV検索をスキップ）
2. CSV検索で品目が見つかれば → 分類＋ルールベースの捨て方を返す
3. 見つからなければ → OpenAI APIで分類を推測 → ルールベースの捨て方を付加して返す
"""

import os
import re
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

# プロンプトテンプレートをファイルから読み込む（***が品目名のプレースホルダー）
_prompt_path = os.path.join(os.path.dirname(__file__), '..', 'Input', 'gomi_bot_prompt.txt')
with open(_prompt_path, encoding='utf-8') as f:
    PROMPT_TEMPLATE = f.read()

# 素材を表すキーワード。これらが入力に含まれる場合はCSVをスキップしてOpenAIに委ねる
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


_MATERIAL_KEYWORDS = [
    '金属', '鉄', 'アルミ', 'ステンレス', '銅', '真鍮',
    '木', '木製', '木材', '竹', '竹製',
    'プラスチック', '樹脂', 'ナイロン', 'ポリ',
    'ガラス', '陶器', '陶磁器', '磁器',
    '布', '綿', '革', 'レザー', 'ゴム',
    '紙', '段ボール',
]


def _has_material_keyword(text: str) -> bool:
    """入力テキストに素材を表すキーワードが含まれるか判定する。"""
    return any(kw in text for kw in _MATERIAL_KEYWORDS)


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


def ask_openai(item: str) -> str:
    """
    CSVで見つからなかった品目をOpenAI APIで分類する。

    引数:
        item: ユーザーが入力した品目名

    戻り値:
        AIの生の回答テキスト（例: "【分類】燃やせる" または素材確認の質問）
    """
    prompt = PROMPT_TEMPLATE.replace('***', item)

    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[
            {'role': 'user', 'content': prompt}
        ],
        max_tokens=100,
        temperature=0,
    )

    return response.choices[0].message.content.strip()


def _parse_ai_response(item: str, ai_response: str) -> str:
    """
    AIの回答を解析してLINE返信メッセージを返す。

    - 【分類】〇〇 が含まれる場合 → 分類名を抽出してルールベースの捨て方を付加
    - 質問が含まれる場合（素材確認など）→ そのまま返す
    - それ以外 → フォールバックメッセージ
    """
    # 素材確認などの質問が含まれる場合はそのまま返す
    if '？' in ai_response or '?' in ai_response:
        return ai_response

    # 【分類】〇〇 を抽出
    match = re.search(r'【分類】(.+)', ai_response)
    if match:
        category = match.group(1).strip()
        if category == '不明':
            return (
                f'「{item}」の分類を判定できませんでした。\n'
                f'安城市の分別ガイドをご確認いただくか、市役所にお問い合わせください。'
            )
        return _format_response(item, category)

    # パース失敗時のフォールバック
    return (
        f'「{item}」の分類をAIが判定できませんでした。\n'
        f'安城市の分別ガイドをご確認いただくか、市役所にお問い合わせください。'
    )


def classify_selection(result: CandidatesResult, choice: int) -> str:
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
        ai_response = ask_openai(result.query)
        return _parse_ai_response(result.query, ai_response)
    return f'1〜{len(result.candidates) + 1}の番号で選んでください。'


def classify(item: str) -> str | CandidatesResult:
    """
    品目名を受け取り、分類結果のメッセージまたは候補リストを返す。

    引数:
        item: ユーザーが入力した品目名（例: "ペットボトル"）

    戻り値:
        str: 分類が確定した場合のLINE返信メッセージ
        CandidatesResult: 候補が複数ある場合（main.pyでセッション管理）
    """
    # ① 素材キーワードが含まれる場合はCSVをスキップしてOpenAIに委ねる
    if _has_material_keyword(item):
        ai_response = ask_openai(item)
        return _parse_ai_response(item, ai_response)

    # ② CSV完全一致・高確信度一致
    result = search_item(item, ITEMS_TO_CATEGORY)
    if result:
        category, matched_item = result
        return _format_response(matched_item, category)

    # ③ 部分一致候補を収集
    candidates = get_candidates(item, ITEMS_TO_CATEGORY)
    if len(candidates) >= 2:
        return CandidatesResult(query=item, candidates=candidates)
    if len(candidates) == 1:
        matched_item, category = candidates[0]
        return _format_response(matched_item, category)

    # ④ OpenAI にフォールバック
    ai_response = ask_openai(item)
    return _parse_ai_response(item, ai_response)


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
