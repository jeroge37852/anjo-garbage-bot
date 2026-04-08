"""
ゴミ分類を判定するモジュール。

処理の流れ:
1. CSV検索で品目が見つかれば → そのまま分類を返す
2. 見つからなければ → OpenAI APIに各分類の定義を渡して推測してもらう
"""

import os
from openai import OpenAI
from dotenv import load_dotenv
from app.data_loader import load_garbage_data, search_item

# .envファイルからAPIキーを読み込む
load_dotenv()

# OpenAIクライアントの初期化（APIキーは.envから自動で読まれる）
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# アプリ起動時に一度だけCSVを読み込む（毎回読むと遅いため）
ITEMS_TO_CATEGORY, CATEGORY_DEFINITIONS = load_garbage_data()

# OpenAIに渡す「分類の定義」テキストを事前に作成しておく
# 定義がない分類にはデフォルトの説明を補足する
CATEGORY_SUPPLEMENT = {
    '燃やせる':      '生ごみ、紙くず、布製品など、燃やして処理できるもの。',
    '燃やせるごみ':  '生ごみ、紙くず、布製品など、燃やして処理できるもの。',
    '燃やせない':    'ガラス、陶磁器、金属製品など、燃やせないもの。',
    '危険ゴミ':      '電池（乾電池・ボタン電池・リチウムイオン電池）、充電式小型家電（スマートフォン・モバイルバッテリーなど1辺20cm以下）。',
    '粗大ごみ':      'ゴミ袋に入らない大きな家具・家電・自転車など。',
    '購入店・販売店へ': '車のバッテリー、タイヤなど、販売店に引き取ってもらうもの。',
}


def build_definitions_text() -> str:
    """
    OpenAIへ渡す「分類の定義一覧」テキストを作成する。
    """
    lines = []
    # CSV内の定義
    for category, definition in CATEGORY_DEFINITIONS.items():
        lines.append(f'・{category}: {definition}')
    # 補足定義（CSV定義がない分類）
    for category, definition in CATEGORY_SUPPLEMENT.items():
        if category not in CATEGORY_DEFINITIONS:
            lines.append(f'・{category}: {definition}')
    return '\n'.join(lines)


DEFINITIONS_TEXT = build_definitions_text()


def ask_openai(item: str) -> str:
    """
    CSVで見つからなかった品目をOpenAI APIで推測する。

    引数:
        item: ユーザーが入力した品目名

    戻り値:
        推測した分類と理由を含むテキスト（str）
    """
    # 分類名の一覧（OpenAIに選ばせる候補）
    category_list = ', '.join(
        list(CATEGORY_DEFINITIONS.keys()) + list(CATEGORY_SUPPLEMENT.keys())
    )

    prompt = f"""あなたは安城市のゴミ分別アシスタントです。
以下の分類定義を参考にして、ユーザーが入力した品目がどの分類に当たるか答えてください。

【分類の定義】
{DEFINITIONS_TEXT}

【分類の候補】
{category_list}

【ルール】
- 必ず上記の分類候補の中から最も適切なものを1つ選んでください。
- 回答は「分類: ○○」から始めてください。
- 次の行に短い理由（1〜2文）を書いてください。
- 判断が難しい場合は「燃やせる」を選んでください。

ユーザーの入力: 「{item}」
"""

    response = client.chat.completions.create(
        model='gpt-4o-mini',   # 安くて十分な精度のモデル
        messages=[
            {'role': 'user', 'content': prompt}
        ],
        max_tokens=200,
        temperature=0,  # 0にすると毎回同じ答えが返る（安定性重視）
    )

    return response.choices[0].message.content.strip()


def classify(item: str) -> str:
    """
    品目名を受け取り、分類結果のメッセージを返す。
    LINE Botのメッセージとして直接使える形式にする。

    引数:
        item: ユーザーが入力した品目名（例: "ペットボトル"）

    戻り値:
        LINEに返すメッセージ文字列
    """
    # ① CSV検索
    category = search_item(item, ITEMS_TO_CATEGORY)

    if category:
        # CSV で見つかった場合
        definition = CATEGORY_DEFINITIONS.get(category, '')
        msg = f'「{item}」は【{category}】です。\n'
        if definition:
            msg += f'\n📋 定義:\n{definition}'
        return msg
    else:
        # ② OpenAI にフォールバック
        ai_answer = ask_openai(item)
        return (
            f'「{item}」はCSVに登録されていないため、AIが推測しました。\n\n'
            f'{ai_answer}\n\n'
            f'※ 正確な情報は安城市の分別ガイドをご確認ください。'
        )


# --- 動作確認用 ---
if __name__ == '__main__':
    test_items = [
        'ペットボトル',   # CSV にある
        'スマートフォン', # CSV にある
        '電子レンジ',     # CSV にある（粗大ごみ）
        '壊れたドライヤー', # CSV にない → OpenAI
        'マスク',         # CSV にない → OpenAI
    ]

    for item in test_items:
        print(f'=== 入力: {item} ===')
        result = classify(item)
        print(result)
        print()
