# 2026年11月9日 更新版（N177）

11月1日に添付された `drc_mobility-main (8).zip` を基準に、前回の修正と今回の更新をまとめました。前回のZIPを適用していなくても、この差分だけで更新できます。削除するファイルはありません。

## ダウンロードとGitHub更新

- 全体ZIP：更新後のプロジェクト一式です。バックアップ・ローカル確認用です。
- 差分ZIP：元の `(8)` から変更したファイルだけです。GitHubへはこちらを適用してください。100ファイル未満です。

1. 差分ZIPを解凍し、`mosuzuki/drc_mobility` の `main` ブランチのトップで **Add file → Upload files** を開きます。
2. 解凍した中身を、`assets/`、`data/`、`scripts/`、`raw/`、`extracted/`、`.github/` の構成を保ってアップロードし、コミットします。ZIPそのものや、解凍時の外側のフォルダーはアップロードしません。
3. `.github/workflows/pages.yml` と `.github/workflows/update-sitrep.yml` も上書きします。macOSで非表示の場合は Command + Shift + . で表示できます。
4. **Settings → Pages → Source: GitHub Actions** を確認し、**Actions** のPagesデプロイ完了を確認します。続けて **Auto-update latest INSP SitRep → Run workflow** を1回実行してください。スケジュールが無効化されていれば有効にします。
5. 公開ページを再読み込みし、**N177・報告日2026年11月7日・確定10,920例・死亡5,323例** を確認してください。

実際のGitHubへのコミットや公開は、このファイル納品には含みません。

## 反映範囲

| 対象 | 反映内容 |
|---|---|
| DRC | N160〜N177を連続収録。N172は保健省公式PDFで補完。過去の欠番N142も保健省から補完 |
| 最新DRC | N177、11月7日報告・11月8日公表。累積10,920例・死亡5,323例。当日41例・死亡21例 |
| ウガンダ | WHO・Africa CDCの8月27日終息確認を維持。累積確定20例・死亡2例。未取得の日別情報をゼロとみなさない |
| ケニア | WHO表11月5日時点で9例・死亡2例。11月3日時点の回復5人、治療中2人、観察中接触者52人を説明に反映 |
| ナイジェリア | NCDC 11月5日報告の確定1例、接触者62人・観察終了7人・陽性転化0人を反映。死亡0例は10月29日の確認値であり、死亡数の基準日を別に保持 |
| 欧州 | フランスの回復、ドイツ・オランダ・スイスへの搬送に加え、11月5日の英国への確定患者1人の医療搬送を反映。搬送元と二重計上しない |

## 自動更新の修正

- フランス語の日付 `1er novembre`、`Date du rapport`、表の列ずれ、改ページ、折り返し名、上下にずれたKPIカードに対応。
- INSPの記事・メディア一覧に加え、DRC保健省の公式メディア一覧も検索。最新だけでなく未取得報告も再試行します。
- 累積数の訂正と当日の新規報告を別々に保存します。N169の死亡+314の遡及修正、N170の-314の再修正、N173の症例+3の修正を新規発生数に混ぜません。
- N177原表のNord-Kivu当日死亡は州9例、保健区域別合計8例です。差の1例は区域未配分として保存し、全国・州値を維持。区域を推測して配分しません。この確認済み例外以外の不一致は検証エラーになります。
- 更新処理は6時間ごとの取得後に予測・検証まで実行します。更新botのコミット後もPagesを再デプロイする設定を含みます。取得失敗時は直前の検証済みデータを保持し、警告を残します。
- 国別情報では報告日・公表日・死亡数の確認日を区別。別ソースの古い公表日や説明が最新値に残る問題も修正しました。

## 予測・検証

N177と補完済み履歴を基準に、7/14/21/28日予測（LightGBM・分岐過程・アンサンブル）、260日規模の4シナリオ、報告補正後感染規模、Rt、CFRを再計算しました。既存の不確実性区間・モデルの制約表示を保持しています。任意機能Chronos-2は未導入のため利用不可と明示されます。

PDF解析、日別・累積集計、国別データ、予測整合性・時系列分割、描画、フロントエンド整合性の検証を実施。Chromiumで最新KPI、8グラフ、4予測期間・4シナリオ切替を確認しました。JavaScript実行エラーはなく、11月1〜7日の新規報告集計は450例・死亡219例です。

検証環境では実CDNファイルを取得して補いました。地図の症例レイヤーは生成されますが、背景タイルの外部通信はこの環境で確認できません。実リポジトリのGitHub Actions実行は適用後に確認してください。

## 公式出典

- [INSP SitRep一覧](https://insp.cd/category/sitrep/)
- [INSP N177](https://insp.cd/wp-content/uploads/2026/11/SitRep_MVE_RDC_N177_07-11-2026_final_corrige.pdf)
- [DRC保健省 N172](https://administration.sante.gouv.cd/wp-content/uploads/2026/11/SitRep_MVE_RDC_N_172_02-11-2026.pdf)
- [WHO ウガンダ終息](https://www.afro.who.int/countries/uganda/news/uganda-ends-ebola-outbreak-following-completion-42-day-countdown)
- [WHO 国別表](https://www.who.int/emergencies/alert-and-response)
- [WHO 11月6日状況報告](https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON625)
- [ECDC 11月6日](https://www.ecdc.europa.eu/en/public-health-events/2026-eip-00026/2026-11-06)
- [NCDC 11月5日PDF](https://ncdc.gov.ng/themes/common/files/sitreps/0e3d9443f4e7b6a58631f941ab72eb48.pdf)

11月1日版の説明書は過去の変更記録として同梱しています。今回の適用手順・最新値はこちらを参照してください。
