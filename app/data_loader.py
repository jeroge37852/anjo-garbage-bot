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
import unicodedata

# CSVファイルのパス（このファイルから見た相対パス）
CSV_PATH = os.path.join(os.path.dirname(__file__), '..', 'Input', '安城市ゴミ分別.csv')


def _normalize(text: str) -> str:
    """
    検索用に文字列を正規化する。

    - NFKC正規化: 半角カナ→全角カナ、全角英数→半角英数
    - カタカナ→ひらがなに統一
    - 大文字→小文字
    - 前後の空白を除去
    """
    # NFKC正規化（半角カナ→全角カナ など）
    text = unicodedata.normalize('NFKC', text)
    # カタカナ→ひらがなに統一（ァ-ン の範囲）
    text = ''.join(
        chr(ord(c) - 0x60) if 'ァ' <= c <= 'ン' else c
        for c in text
    )
    return text.lower().strip()


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

    照合の優先順位:
      1. 完全一致（そのまま / 正規化後）
      2. 後方一致: 品目名の末尾がクエリと一致
         例: "飲料用のペットボトル".endswith("ペットボトル")
      3. 前方一致: 品目名にクエリが含まれる
         マッチ率（クエリ長/品目名長）が高い順に選ぶ
      4. 逆方向部分一致: クエリに品目名が含まれる
         より長い品目名（より具体的）を優先

    正規化により半角カナ・ひらがな・大小文字の表記ゆれにも対応する。

    引数:
        query: ユーザーが入力した品目名（例: "ペットボトル"）
        items_to_category: load_garbage_data()で取得した品目辞書

    戻り値:
        見つかった場合: 分類名（str）
        見つからない場合: None
    """
    nq = _normalize(query)

    # 1. 完全一致
    if query in items_to_category:
        return items_to_category[query]
    for item, category in items_to_category.items():
        if _normalize(item) == nq:
            return category

    # 2. 後方一致: 品目名の末尾がクエリと一致
    #    例: "飲料用のペットボトル" が "ペットボトル" で終わる → 優先
    suffix_matches = [
        (item, category)
        for item, category in items_to_category.items()
        if _normalize(item).endswith(nq)
    ]
    if suffix_matches:
        # 最も短い品目名（最も直接的）を返す
        suffix_matches.sort(key=lambda x: len(x[0]))
        return suffix_matches[0][1]

    # 3. 前方一致: 品目名にクエリが含まれる
    #    マッチ率（クエリ長/品目名長）が高い順に選ぶ
    forward_matches = [
        (len(nq) / len(_normalize(item)), item, category)
        for item, category in items_to_category.items()
        if nq in _normalize(item)
    ]
    if forward_matches:
        forward_matches.sort(key=lambda x: -x[0])
        return forward_matches[0][2]

    # 4. 逆方向部分一致: クエリに品目名が含まれる
    #    例: "指定袋入りペットボトル" → "飲料用のペットボトル" の末尾が一致
    #    より長い品目名（より具体的）を優先
    reverse_matches = [
        (len(item), item, category)
        for item, category in items_to_category.items()
        if _normalize(item) in nq
    ]
    if reverse_matches:
        reverse_matches.sort(key=lambda x: -x[0])
        return reverse_matches[0][2]

    # 5. 共通末尾一致: クエリと品目名が同じ末尾を共有する
    #    例: "使用済みペットボトル" と "飲料用のペットボトル" は "ペットボトル" を共有
    #    共通末尾が3文字以上で、最も長く一致するものを返す
    def _common_suffix_len(s1: str, s2: str) -> int:
        length = 0
        for c1, c2 in zip(reversed(s1), reversed(s2)):
            if c1 == c2:
                length += 1
            else:
                break
        return length

    suffix_matches = [
        (_common_suffix_len(nq, _normalize(item)), item, category)
        for item, category in items_to_category.items()
    ]
    best_len, best_item, best_category = max(suffix_matches, key=lambda x: x[0])
    if best_len >= 3:
        return best_category

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
