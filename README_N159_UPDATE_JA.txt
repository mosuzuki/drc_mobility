N117–N159 SitRep復元パッチ（2026-10-23）
==================================
GitHubリポジトリ直下に、本ZIP内のファイルを上書きしてください。
既存の大きなフォルダや既存データファイルを消さないでください。

全国・州・ヘルスゾーン別の公式累積値をN159（2026-10-20）まで再構築。
更新: report_summary / cases_by_hz / cases_unventilated /
response_indicators / contact_followup_by_province / health_zone_activity_status /
latest_situation / final_size_projection / true_infection_estimate /
rt_estimates / cfr_estimates。

注意事項
- N142（2026-10-03）のPDFは提供ZIPに存在しません。推測補完しません。
- N150（2026-10-11）の公式Table 1は9,065例、Table 2は9,066例
  （Haut-Uélé: 382 vs 383）と1例矛盾します。全国値はTable 1を採用し、
  N150当日のヘルスゾーン別スナップショットは作りません。
- N159の66ヘルスゾーンのうち、7ゾーン（計19例）は既存地図に
  ジオメトリ情報がありません。全国集計には含み、地図上は描画しません。
- 一部の死亡例は保健区への帰属未確定（N159で493死亡例）。
  cases_unventilated.csvに計上し、保健区に勝手に配分しません。
- ウガンダはWHO確認済みの終息状態を維持しています。
- GitHub Actionsで現行形式のINSP PDFを取得した場合にも、
  厳密な州別・保健区別の合計検証を実行します。

更新後は Actions → Auto-update latest INSP SitRep → Run workflow を実行し、
自動処理のログ・データ更新日を確認してください。
