"""
ゴミ分類を判定するモジュール。

処理の流れ:
1. CSV検索で品目が見つかれば → そのまま分類を返す
2. 見つからなければ → Input/gomi_bot_prompt.txt のプロンプトでOpenAI APIに問い合わせ
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

# プロンプトテンプレートをファイルから読み込む（***が品目名のプレースホルダー）
_prompt_path = os.path.join(os.path.dirname(__file__), '..', 'Input', 'gomi_bot_prompt.txt')
with open(_prompt_path, encoding='utf-8') as f:
    PROMPT_TEMPLATE = f.read()


def ask_openai(item: str) -> str:
    """
    CSVで見つからなかった品目をOpenAI APIで推測する。

    引数:
        item: ユーザーが入力した品目名

    戻り値:
        分類と捨て方を含むテキスト（str）
    """
    prompt = PROMPT_TEMPLATE.replace('***', item)

    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[
            {'role': 'user', 'content': prompt}
        ],
        max_tokens=600,
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
