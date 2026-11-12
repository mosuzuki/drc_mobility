# Bundibugyo型エボラ：多国間ダッシュボード

DRCの流行状況、周辺国の確定例、人口移動・対応指標、推定・予測をまとめた静的Webサイトです。

2026年11月12日更新：**N180（11月10日報告／11月11日公表）、DRC確定11,114例・死亡5,433例**。最新値の正本は `data/report_summary.csv`、国別情報は `data/international_cases.json` です。予測は最新の検証済みデータから生成します。

## 構成

| 場所 | 役割 |
|---|---|
| `index.html`, `assets/` | 画面・地図・グラフ、日本語／英語／フランス語 |
| `data/` | 表示用データ、推定・予測結果 |
| `scripts/` | 取得、検証、推定、公開用ビルド |
| `raw/` | 入手した公式PDF原本。検証・再解析のため保持 |
| `extracted/` | 抽出テキスト、出典確認・照合記録 |
| `.github/workflows/` | 定期更新とGitHub Pagesへの公開 |

方法・データの制約は [DATA_NOTES.md](DATA_NOTES.md)、今回の整理内容は [CHANGELOG.md](CHANGELOG.md) を参照してください。

## ローカル表示

```bash
python -m http.server 8000
```

ブラウザーで http://localhost:8000/ を開きます。HTMLの直接ダブルクリックではデータを取得できません。

## 更新と検証

Python 3.11以上、Node.js、Popplerの `pdftotext` を使用します。

```bash
pip install -r scripts/requirements.txt
python -m playwright install chromium
python scripts/update_dashboard_all.py
```

ネットワークを使わず既存データから推定・予測を再計算する場合：

```bash
python scripts/update_dashboard_all.py --offline
```

手元のPDFを取り込む場合：

```bash
python scripts/update_from_insp_sitrep.py --pdf path/to/SitRep.pdf --force
python scripts/update_dashboard_all.py --offline
```

`repair_latest_sitrep_values.py` は過去のN44～N58等の補正を再現する保守用処理です。毎回の更新では実行しません。`prepare_data.py` は移動データの変換用です。

## GitHubへ反映

今回の整理では削除もあるため、全体版を新しいフォルダーへ解凍して使うか、差分版の `APPLY_UPDATE_JA.md` に従って更新してください。古いフォルダーへの単純な上書きだけでは不要ファイルが残ります。

GitHub Pagesの **Settings → Pages → Source** は **GitHub Actions** を選択します。`.github/workflows/` の2ファイルも必ず更新してください。通常のファイルアップロードでは非表示フォルダーが漏れる場合があります。

- `Auto-update latest INSP SitRep`：6時間ごとにINSP・保健省を確認し、欠番取得、周辺国更新、推定・予測、検証を順に実行します。
- `Deploy static dashboard to GitHub Pages`：通常のpushおよび自動更新成功後に公開します。
- `scripts/build_site.py`：`_site/` に画面が参照するファイルだけを配置します。PDF・抽出記録・スクリプトは公開用配信物に含めません。
- 取得失敗時は直前の検証済みデータを保持し、更新ログとレビュー通知を残します。検証に失敗した結果はコミットしません。

OpenAIによる補助要約・旧形式PDF解析は任意です。`OPENAI_API_KEY` がなくても現行SitRepの規則による解析、予測、表示は動作します。Chronos-2も任意機能で、標準構成では利用不可と表示します。

公開先へのコミットはこのZIP納品には含まれません。適用後、Actionsで手動更新を一度実行し、Pagesの更新完了を確認してください。
