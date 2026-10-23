# 多国間KPI更新パッチ（2026-10-23）

## 適用対象
N159版に機械学習予測パッチと、その後の短期・最終規模予測表示修正パッチを適用したリポジトリ。
パッチZIPの**中身**をリポジトリ直下へ上書きする。変更は9ファイルのみ（既存の流行予測データには変更なし）。

## 画面
- KPIは「DRC確定例、DRC確定死亡例、ウガンダ、ケニア、ナイジェリア」の5枚。
- ウガンダの1枚には確定例20例、死亡2例、終息状態を表示。ウガンダは以前からの公式データを保持。
- ケニアは9確定例・1確定死亡例（ECDC 2026-10-22）。ナイジェリアは1確定例・0確定死亡例（ECDC / CDC、症例確認2026-10-20）。
- 「最新の状況」にケニア・ナイジェリアを追加。「その他」にはフランス輸入例とドイツ・オランダへの医療搬送（現地伝播とは区別）を表示。
- 日本語/英語/フランス語対応。モバイルではカードを縦に並べる。

## 自動更新
- `data/international_cases.json` は、新規国別情報用の機械可読データ。出典URL、報告基準日、ソース公表日を別々に保持。
- `scripts/update_international_cases.py` はECDC流行情報の最新更新を確認し、国見出し以下の症例数と死亡数だけを厳格に読み取る。取得できなければNICDの公開国別表を照合。
- 取得できない、数字が曖昧、公開日が古い場合は、**前回確認した数値を保持する**。取得できないケースをゼロ例として扱わない。
- `scripts/update_dashboard_all.py` に組み込んだため、既存のGitHub Actionsを手動実行するか、定期実行されると国別データも更新する。
- 新しい症例が確認されても、感染経路の詳しい説明を自動で推測しない。数値だけ更新して「原文を確認」と表示する。
- サイトのDOMや文章形式が変われば更新が止まることはありうる。取得を無検証で成功扱いせず、`.international_cases_status.md` に状態を記録する。

## ソース
- ECDC 2026-10-22: https://www.ecdc.europa.eu/en/public-health-events/2026-eip-00026/2026-10-22
- ECDC更新一覧: https://www.ecdc.europa.eu/en/public-health-events/2026-EIP-00026
- US CDC: https://www.cdc.gov/ebola/situation-summary/
- NICD: https://www.nicd.ac.za/bundibugyo-virus-disease-updates/

## 事前確認

```
python scripts/test_international_cases.py
node scripts/test_frontend_data_consistency.cjs
node scripts/test_forecast_render.cjs
python scripts/validate_dashboard_outputs.py
```

注：当地のネットワーク制限下ではECDC/NICDへのGitHub Actions実行を確認できていない。実際のネットワーク取得の成否はGitHub Actionsで確認すること。
