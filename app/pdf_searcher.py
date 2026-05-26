"""
PDFから品目を検索し、該当ページをPNG画像として提供するモジュール。

対象PDF:
  - wakekata-j240301.pdf  （分け方・出し方 早わかりブック）
  - wakekata-j2407kouhan.pdf（分け方・出し方 後半版）
"""

import os
import fitz  # PyMuPDF

_DIR = os.path.join(os.path.dirname(__file__), '..', 'Input')

_PDFS: dict[str, dict] = {
    'wakekata-j240301': {
        'path': os.path.join(_DIR, 'wakekata-j240301.pdf'),
        'display': '分け方・出し方 早わかりブック',
    },
    'wakekata-j2407kouhan': {
        'path': os.path.join(_DIR, 'wakekata-j2407kouhan.pdf'),
        'display': '分け方・出し方（後半版）',
    },
}

# 起動時にテキストインデックスを構築: {pdf_name: [(page_num, normalized_text), ...]}
_index: dict[str, list[tuple[int, str]]] = {}


def _build_index() -> None:
    for name, info in _PDFS.items():
        doc = fitz.open(info['path'])
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text()
            normalized = ''.join(text.split())  # 空白・改行を除去して縦書きにも対応
            pages.append((i + 1, normalized))
        _index[name] = pages


_build_index()


def search_pdfs(item: str) -> list[tuple[str, int, str]]:
    """
    両PDFで品目名を検索し、出現回数が最も多いページを返す（TOCページを避けるため）。

    戻り値: [(pdf_name, page_num, display_name), ...]
    """
    item_normalized = ''.join(item.split())
    results = []
    for pdf_name, pages in _index.items():
        best_page, best_count = None, 0
        for page_num, normalized in pages:
            count = normalized.count(item_normalized)
            if count > best_count:
                best_count, best_page = count, page_num
        if best_page is not None:
            results.append((pdf_name, best_page, _PDFS[pdf_name]['display']))
    return results


def render_page_png(pdf_name: str, page_num: int, item: str = '') -> bytes:
    """指定PDFの指定ページをPNG画像（バイト列）として返す。itemを指定するとその箇所をハイライトする。"""
    path = _PDFS[pdf_name]['path']
    doc = fitz.open(path)
    page = doc[page_num - 1]  # fitz は 0-indexed

    if item:
        rects = page.search_for(item)
        for rect in rects:
            annot = page.add_highlight_annot(rect)
            annot.update()

    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom で高解像度
    return pix.tobytes('png')
