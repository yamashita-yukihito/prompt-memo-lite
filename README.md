# Prompt Memo Lite

**Prompt Memo Lite** は、Stable Diffusion などの画像生成プロンプト編集を想定した、軽量なメモ帳 + フォルダ横断検索ツールです。

`memo.pyw` は `positive.txt` / `negative.txt` を素早く編集するための小さなメモ帳で、`memo_search.pyw` はフォルダ内の `.txt` / `.md` / `.py` / `.json` などを本文検索するための検索専用アプリです。


## Features

### memo.pyw

- Python 標準ライブラリのみで動作
- `positive.txt` / `negative.txt` の2ファイルを切り替えて編集
- `Ctrl+Tab` で positive / negative を切り替え
- `Ctrl+S` で保存
- 5分ごとの自動保存
- 保存時に前回内容を日付フォルダへバックアップ
- `Ctrl+F` で現在のメモ内検索
- `Ctrl+Shift+F` で `memo_search.pyw` を起動してフォルダ横断検索
- 選択文字列に `Ctrl+Up` / `Ctrl+Down` で重み付け
- 選択文字列に `Ctrl+[` で `{text|}` 形式に変換
- `Ctrl+クリック` でカンマ区切りプロンプトを自動選択
- `Ctrl+1` ～ `Ctrl+9` で `ctrl_1.txt` ～ `ctrl_9.txt` の内容を挿入
- `Ctrl+マウスホイール` でフォントサイズ変更
- コメント、 `{}`、 `|`、カンマの簡易色分け
- フォントサイズやウィンドウ位置を `settings.json` に保存

### memo_search.pyw

- フォルダ内のテキストファイルを横断検索
- 検索ワード入力
- 検索対象フォルダ指定
- 拡張子フィルタ
- 大文字小文字の区別 ON/OFF
- 正規表現検索 ON/OFF
- 検索結果一覧表示
- 前後数行のプレビュー表示
- ダブルクリックで該当ファイルを既定アプリで開く
- 選択結果のパスと行番号をコピー
- `.git`、`__pycache__`、`node_modules`、仮想環境フォルダなどを検索対象から除外
- `utf-8-sig`、`utf-8`、`cp932`、`shift_jis` の読み込みに対応

## Requirements

- Python 3.10 以降
- Windows 推奨
- 追加ライブラリ不要

このツールは Tkinter を使用しています。通常の Python for Windows には Tkinter が含まれています。

## Installation

このリポジトリを好きな場所に配置します。

```bash
git clone https://github.com/yamashita-yukihito/prompt-memo-lite.git
cd prompt-memo-lite
```

Windows では `.pyw` ファイルをダブルクリックして起動できます。

```text
memo.pyw
memo_search.pyw
```

## Basic usage

### 1. メモ帳を起動する

`memo.pyw` を起動します。

初回起動時、同じフォルダに以下のファイルがなければ自動で作成されます。

```text
positive.txt
negative.txt
```

### 2. positive / negative を切り替える

`Ctrl+Tab` で `positive.txt` と `negative.txt` を切り替えます。

### 3. 保存する

`Ctrl+S` で保存します。

保存時、変更前の内容はバックアップとして保存されます。

### 4. フォルダ横断検索する

`Ctrl+Shift+F` を押すと `memo_search.pyw` が起動します。

メモ帳側で文字列を選択している場合、その文字列が検索ワードとして検索アプリに渡されます。

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+S` | 保存 |
| `Ctrl+Tab` | positive / negative 切り替え |
| `Ctrl+Shift+Tab` | 逆方向に切り替え |
| `Ctrl+F` | 現在のメモ内検索 |
| `Ctrl+Shift+F` | フォルダ横断検索アプリを起動 |
| `Ctrl+Up` | 選択範囲の重みを上げる |
| `Ctrl+Down` | 選択範囲の重みを下げる |
| `Ctrl+[` | 選択範囲を `{text|}` に変換 |
| `Ctrl+Left` | 前のカンマ区切りブロックを選択 |
| `Ctrl+Right` | 次のカンマ区切りブロックを選択 |
| `Ctrl+Click` | カンマ区切りブロックを選択 |
| `Ctrl+1` ～ `Ctrl+9` | `ctrl_1.txt` ～ `ctrl_9.txt` の内容を挿入 |
| `Ctrl+MouseWheel` | フォントサイズ変更 |

## File layout

```text
prompt-memo-lite/
├─ memo.pyw
├─ memo_search.pyw
├─ positive.txt
├─ negative.txt
├─ settings.json
├─ memo_search_settings.json
├─ ctrl_1.txt
├─ ctrl_2.txt
└─ ...
```

`positive.txt`、`negative.txt`、`ctrl_*.txt`、`settings.json`、`memo_search_settings.json` はユーザーごとの作業データです。

公開リポジトリに個人用プロンプトを含めたくない場合は、これらを `.gitignore` に入れてください。

## Recommended .gitignore

```gitignore
# User data
positive.txt
negative.txt
ctrl_*.txt
settings.json
memo_search_settings.json

# Backups
backup/
backups/

# Python cache
__pycache__/
*.pyc

# OS files
.DS_Store
Thumbs.db
```

## Design policy

このツールは、機能を増やしすぎないことを重視しています。

- `memo.pyw` は編集専用
- `memo_search.pyw` は検索専用
- 連携は `Ctrl+Shift+F` だけ
- 追加ライブラリなし
- できるだけ軽く、壊れにくくする

検索機能をメモ帳本体に詰め込まず、別アプリとして分けることで、メモ帳側のキー操作や選択処理を安定させやすくしています。

## Notes

- `memo_search.pyw` は保存済みファイルを検索します。
- メモ帳で未保存の変更がある場合は、検索前に `Ctrl+S` してください。
- 大量のファイルを検索する場合、初回検索に時間がかかることがあります。
- 現在はインデックス検索ではなく、対象フォルダを毎回走査するシンプルな方式です。

## License

MIT License です。詳細は [LICENSE](LICENSE) を参照してください。
