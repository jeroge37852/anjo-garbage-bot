"""
CSVファイルを読み込んで、ゴミ分別データを辞書形式で提供するモジュール。

データ構造:
- items_to_category: 品目名 → 分類名 の辞書
  例: {"ペットボトル": "拠点回収品目", "生ごみ": "燃やせる", ...}

- category_definitions: 分類名 → 定義テキスト の辞書
  例: {"資源(プラスチック)": "100%プラスチックでできている...", ...}
"""

import csv
import os

# CSVファイルのパス（このファイルから見た相対パス）
CSV_PATH = os.path.join(os.path.dirname(__file__), '..', 'Input', '安城市ゴミ分別.csv')


def load_garbage_data():
    """
    CSVを読み込んで2つの辞書を返す。

    戻り値:
        items_to_category (dict): 品目名 → 分類名
        category_definitions (dict): 分類名 → 定義テキスト
    """
    items_to_category = {}      # 品目 → 分類
    category_definitions = {}   # 分類 → 定義

    with open(CSV_PATH, encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)  # ヘッダー行をスキップ

        for row in reader:
            # 列数が足りない行はスキップ
            if len(row) < 4:
                continue

            category = row[0].strip()   # 知りたい分類
            definition = row[1].strip() # 定義
            item = row[3].strip()       # 品目

            # 分類名が空の行はスキップ
            if not category:
                continue

            # 定義を保存（上書きしないよう、まだない場合のみ）
            if category not in category_definitions and definition:
                category_definitions[category] = definition

            # 品目が空でなければ登録
            if item:
                # 品目名を正規化（全角スペース・括弧内の説明を除去せずそのまま登録）
                items_to_category[item] = category

    return items_to_category, category_definitions


def search_item(query: str, items_to_category: dict) -> str | None:
    """
    ユーザーが入力した文字列をCSVの品目と照合する。

    完全一致 → 部分一致（品目名にqueryが含まれる）の順で検索する。

    引数:
        query: ユーザーが入力した品目名（例: "ペットボトル"）
        items_to_category: load_garbage_data()で取得した品目辞書

    戻り値:
        見つかった場合: 分類名（str）
        見つからない場合: None
    """
    # 1. 完全一致を試みる
    if query in items_to_category:
        return items_to_category[query]

    # 2. 部分一致：品目名にqueryが含まれているものを探す
    for item, category in items_to_category.items():
        if query in item:
            return category

    # 3. 逆方向の部分一致：queryに品目名が含まれているものを探す
    #    例: "使用済みペットボトル" と入力 → "ペットボトル" でヒット
    for item, category in items_to_category.items():
        if item in query:
            return category

    return None  # 見つからなかった


# --- 動作確認用 ---
if __name__ == '__main__':
    items, definitions = load_garbage_data()

    print(f'登録品目数: {len(items)}')
    print(f'分類数: {len(definitions)}')
    print()

    # テスト検索
    test_queries = ['ペットボトル', '生ごみ', 'ハンガー', '電池', 'スマートフォン', '存在しない品目']
    for q in test_queries:
        result = search_item(q, items)
        print(f'「{q}」→ {result}')
